mod mermaid_view;
mod protocol;

use std::io;
use std::panic;
use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;

use anyhow::{Context, Result};
use crossterm::{
    cursor::Show,
    event::{DisableMouseCapture, EnableMouseCapture, Event as CtEvent, EventStream, KeyCode},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use futures::{FutureExt, StreamExt};
use mermaid_view::MermaidView;
use protocol::{
    read_harness_events, FileEntry, HarnessCommand, HarnessEvent, RunSummary, VariableEntry,
};
use ratatui::{
    backend::CrosstermBackend,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    widgets::{Block, Borders, Clear, List, ListItem, Paragraph, Wrap},
    Terminal,
};
use tokio::{
    io::{AsyncWriteExt, BufReader},
    process::{ChildStdin, Command},
    sync::mpsc,
};

#[derive(Debug, Clone, PartialEq)]
enum AppMode {
    Chat,
    DraftingSpec,
    SpecReview { spec_text: String },
    Executing,
}

impl AppMode {
    fn label(&self) -> &'static str {
        match self {
            Self::Chat => "chat",
            Self::DraftingSpec => "drafting spec",
            Self::SpecReview { .. } => "spec review",
            Self::Executing => "executing",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
struct AppState {
    mode: AppMode,
    logs: Vec<String>,
    engine: String,
    pct: u16,
    running: bool,
    repo_map_source: Option<String>,
    repo_content: String,
    repo_mode: String,
    repo_focused: bool,
    repo_files: Vec<FileEntry>,
    repo_variables: Vec<VariableEntry>,
    repo_selected: usize,
    context_content: String,
    history_visible: bool,
    history_runs: Vec<RunSummary>,
    history_selected: usize,
    history_detail: Option<String>,
    prompt: String,
    prompt_active: bool,
    assistant_busy: bool,
    deepseek_configured: bool,
    deepseek_source: String,
    memory_path: String,
    preference_count: u32,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            mode: AppMode::Chat,
            logs: vec!["c/p: chat  s: draft spec  m: repository map  d: history  q: quit".into()],
            engine: "idle".into(),
            pct: 0,
            running: false,
            repo_map_source: None,
            repo_content: "Press m to load the repository map.\nThen use r for variables/imports or t for file descriptions.".into(),
            repo_mode: "diagram".into(),
            repo_focused: false,
            repo_files: Vec::new(),
            repo_variables: Vec::new(),
            repo_selected: 0,
            context_content: "Load the repo map to capture the current structure.\nRun history appears under d.".into(),
            history_visible: false,
            history_runs: Vec::new(),
            history_selected: 0,
            history_detail: None,
            prompt: String::new(),
            prompt_active: false,
            assistant_busy: false,
            deepseek_configured: false,
            deepseek_source: "checking".into(),
            memory_path: ".tui_memory.json".into(),
            preference_count: 0,
        }
    }
}

impl AppState {
    fn apply(&mut self, event: HarnessEvent) {
        match event {
            HarnessEvent::Ready { protocol_version } => {
                self.logs
                    .push(format!("bridge ready (protocol {protocol_version})"));
            }
            HarnessEvent::EngineProgress { engine, pct } => {
                self.engine = engine;
                self.pct = pct.min(100);
            }
            HarnessEvent::Log { level, msg } => {
                self.logs.push(format!("[{level}] {msg}"));
            }
            HarnessEvent::ConfigStatus {
                deepseek_configured,
                source,
                memory_path,
                preference_count,
            } => {
                self.deepseek_configured = deepseek_configured;
                self.deepseek_source = source;
                self.memory_path = memory_path;
                self.preference_count = preference_count;
                self.logs.push(format!(
                    "DeepSeek: {} · source={} · {} saved preference(s)",
                    if deepseek_configured {
                        "configured"
                    } else {
                        "not configured"
                    },
                    self.deepseek_source,
                    self.preference_count
                ));
            }
            HarnessEvent::AssistantStatus { stage, busy } => {
                self.assistant_busy = busy;
                self.engine = if busy { stage } else { "idle".into() };
            }
            HarnessEvent::ChatMessage { role, content } => {
                let label = if role == "user" { "you" } else { "assistant" };
                self.logs.push(format!("[{label}] {content}"));
            }
            HarnessEvent::ChatError { stage, message } => {
                self.assistant_busy = false;
                self.engine = "idle".into();
                self.mode = AppMode::Chat;
                self.logs.push(format!("[error] {stage} failed: {message}"));
            }
            HarnessEvent::SpecDraft { text } => {
                self.assistant_busy = false;
                self.engine = "idle".into();
                self.mode = AppMode::SpecReview { spec_text: text };
                self.logs
                    .push("spec draft ready; review it and press y to execute or n to revise".into());
            }
            HarnessEvent::MemoryUpdated {
                preference,
                added,
                count,
            } => {
                self.preference_count = count;
                self.logs.push(if added {
                    format!("[memory] saved preference: {preference}")
                } else {
                    format!("[memory] preference already saved: {preference}")
                });
            }
            HarnessEvent::ContractResult { name, status } => {
                self.logs.push(format!("contract {name}: {status}"));
            }
            HarnessEvent::ContractQueuePlanned { contracts } => {
                self.logs.push(format!(
                    "DeepSeek planned {} contract(s):",
                    contracts.len()
                ));
                self.logs.extend(contracts.into_iter().map(|contract| {
                    let dependencies = if contract.dependencies.is_empty() {
                        "none".into()
                    } else {
                        contract.dependencies.join(", ")
                    };
                    format!(
                        "  {} · {} · dependencies: {dependencies}",
                        contract.name, contract.signature
                    )
                }));
            }
            HarnessEvent::ContractProgress {
                name,
                status,
                attempt,
                worker,
            } => {
                self.logs.push(format!(
                    "contract {name}: {status} · worker={worker} · attempt={attempt}"
                ));
            }
            HarnessEvent::CompileGateResult { status, errors } => {
                self.logs.push(format!("compile gate: {status}"));
                self.logs.extend(errors.into_iter().map(|error| format!("  {error}")));
            }
            HarnessEvent::ProfilingResult {
                loop_order,
                runtime_ns,
                cache_misses,
                spread_ns,
            } => self.logs.push(format!(
                "profile {loop_order}: {runtime_ns}ns (spread {spread_ns}ns, cache misses {})",
                cache_misses
                    .map(|value| value.to_string())
                    .unwrap_or_else(|| "unavailable".into())
            )),
            HarnessEvent::ComputeShieldMetrics {
                phase,
                tokens_baseline,
                tokens_shielded,
                delta,
            } => self.logs.push(format!(
                "compute shield phase {phase}: baseline={tokens_baseline}, shielded={tokens_shielded}, delta={delta}"
            )),
            HarnessEvent::RepoMap { mermaid, summary } => {
                self.repo_map_source = Some(mermaid);
                self.context_content = summary
                    .lines()
                    .take(4)
                    .collect::<Vec<_>>()
                    .join("\n");
                self.repo_content = summary;
                self.repo_mode = "diagram".into();
                self.logs.push("repository map received".into());
            }
            HarnessEvent::RepoMapView { mode, content } => {
                self.repo_mode = mode;
                self.repo_content = content;
            }
            HarnessEvent::RepoMapFiles { entries } => {
                self.repo_files = entries;
                self.clamp_repo_selection();
            }
            HarnessEvent::RepoMapVariables { entries } => {
                self.repo_variables = entries;
                self.clamp_repo_selection();
            }
            HarnessEvent::HistoryList { runs } => {
                self.logs.push(format!("run history: {} run(s)", runs.len()));
                self.history_runs = runs;
                self.history_selected = 0;
                self.history_detail = None;
                self.history_visible = true;
            }
            HarnessEvent::HistoryDetail { run_id, checkpoint } => {
                let body = serde_json::to_string_pretty(&checkpoint)
                    .unwrap_or_else(|_| "<unrenderable checkpoint>".into());
                self.history_detail = Some(format!("run {run_id}\n\n{body}"));
                self.history_visible = true;
                self.logs.push(format!("run detail: {run_id}"));
            }
            HarnessEvent::Result { status, .. } => {
                self.logs.push(format!("result: {status}"));
            }
            HarnessEvent::ProtocolError { line, error } => {
                self.logs
                    .push(format!("[protocol warning] {error}: {line}"));
            }
            HarnessEvent::Done { status } => {
                self.running = false;
                self.engine = "idle".into();
                self.pct = 0;
                if self.mode == AppMode::Executing {
                    self.mode = AppMode::Chat;
                }
                self.logs.push(format!("harness finished: {status}"));
            }
        }
        const MAX_LOGS: usize = 2_000;
        if self.logs.len() > MAX_LOGS {
            self.logs.drain(..self.logs.len() - MAX_LOGS);
        }
    }

    fn clamp_repo_selection(&mut self) {
        let len = self.repo_files.len().max(self.repo_variables.len());
        self.repo_selected = self.repo_selected.min(len.saturating_sub(1));
    }

    fn selected_repo_detail(&self) -> String {
        match self.repo_mode.as_str() {
            "files" => self
                .repo_files
                .get(self.repo_selected)
                .map(|entry| {
                    format!(
                        "{}\n\n{}\n\nsymbols\n{}",
                        entry.path,
                        entry.summary,
                        if entry.symbols.is_empty() {
                            "  none".into()
                        } else {
                            entry
                                .symbols
                                .iter()
                                .map(|symbol| format!("  {symbol}"))
                                .collect::<Vec<_>>()
                                .join("\n")
                        }
                    )
                })
                .unwrap_or_else(|| "No file selected.".into()),
            "variables" => self
                .repo_variables
                .get(self.repo_selected)
                .map(|entry| {
                    format!(
                        "{}\n\nimports\n{}\n\nvariables\n{}",
                        entry.path,
                        indented_or_none(&entry.imports),
                        indented_or_none(&entry.variables)
                    )
                })
                .unwrap_or_else(|| "No file selected.".into()),
            _ => self.repo_content.clone(),
        }
    }
}

fn indented_or_none(values: &[String]) -> String {
    if values.is_empty() {
        "  none".into()
    } else {
        values
            .iter()
            .map(|value| format!("  {value}"))
            .collect::<Vec<_>>()
            .join("\n")
    }
}

struct TerminalGuard;

impl Drop for TerminalGuard {
    fn drop(&mut self) {
        restore_terminal_state();
    }
}

fn restore_terminal_state() {
    let _ = disable_raw_mode();
    let _ = execute!(
        io::stdout(),
        LeaveAlternateScreen,
        DisableMouseCapture,
        Show
    );
}

fn install_terminal_panic_hook() {
    let previous = panic::take_hook();
    panic::set_hook(Box::new(move |panic_info| {
        restore_terminal_state();
        previous(panic_info);
    }));
}

#[tokio::main]
async fn main() -> Result<()> {
    install_terminal_panic_hook();
    let repo_root = std::env::args()
        .nth(1)
        .map(PathBuf::from)
        .unwrap_or(std::env::current_dir()?);
    let python = std::env::var("PYTHON").unwrap_or_else(|_| "python3".into());
    let mut child = Command::new(python)
        .args(["-m", "harness_kernel.tui_bridge"])
        .current_dir(&repo_root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .context("spawn Python harness bridge")?;
    let mut child_stdin = child.stdin.take().context("bridge stdin missing")?;
    let child_stdout = child.stdout.take().context("bridge stdout missing")?;
    let (tx, mut rx) = mpsc::unbounded_channel();
    tokio::spawn(read_harness_events(BufReader::new(child_stdout), tx));

    let result = run_terminal(&mut child_stdin, &mut rx, &repo_root).await;
    let _ = child_stdin.shutdown().await;
    let _ = child.kill().await;
    result
}

async fn run_terminal(
    child_stdin: &mut ChildStdin,
    rx: &mut mpsc::UnboundedReceiver<HarnessEvent>,
    repo_root: &std::path::Path,
) -> Result<()> {
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let _terminal_guard = TerminalGuard;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;
    // Terminal graphics probing must happen after entering the alternate screen
    // and before the crossterm event reader starts consuming terminal replies.
    let mut mermaid = MermaidView::new();
    run_loop(&mut terminal, child_stdin, rx, repo_root, &mut mermaid).await
}

async fn run_loop(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    child_stdin: &mut ChildStdin,
    rx: &mut mpsc::UnboundedReceiver<HarnessEvent>,
    repo_root: &std::path::Path,
    mermaid: &mut MermaidView,
) -> Result<()> {
    let mut state = AppState::default();
    let mut term_events = EventStream::new();
    let mut tick = tokio::time::interval(Duration::from_millis(33));
    loop {
        tokio::select! {
            maybe_event = term_events.next().fuse() => {
                if let Some(Ok(CtEvent::Key(key))) = maybe_event {
                    if state.prompt_active {
                        match key.code {
                            KeyCode::Esc => state.prompt_active = false,
                            KeyCode::Backspace => {
                                state.prompt.pop();
                            }
                            KeyCode::Enter if !state.prompt.trim().is_empty() && !state.assistant_busy => {
                                let text = std::mem::take(&mut state.prompt);
                                send_command(child_stdin, &HarnessCommand::Chat { text }).await?;
                                state.prompt_active = false;
                            }
                            KeyCode::Char(character) => state.prompt.push(character),
                            _ => {}
                        }
                        continue;
                    }
                    match key.code {
                        KeyCode::Char('q') => {
                            send_command(child_stdin, &HarnessCommand::Cancel).await?;
                            break;
                        }
                        KeyCode::Char('y') if matches!(state.mode, AppMode::SpecReview { .. }) => {
                            let spec_text = match &state.mode {
                                AppMode::SpecReview { spec_text } => spec_text.clone(),
                                _ => unreachable!(),
                            };
                            send_command(
                                child_stdin,
                                &HarnessCommand::ExecuteSpec { text: spec_text },
                            )
                            .await?;
                            state.mode = AppMode::Executing;
                            state.running = true;
                            state
                                .logs
                                .push("approved spec sent to the execution pipeline".into());
                        }
                        KeyCode::Char('n') | KeyCode::Esc
                            if matches!(state.mode, AppMode::SpecReview { .. }) =>
                        {
                            state.mode = AppMode::Chat;
                            state
                                .logs
                                .push("spec execution declined; return to chat to revise".into());
                        }
                        KeyCode::Char('s')
                            if state.mode == AppMode::Chat && !state.assistant_busy =>
                        {
                            send_command(child_stdin, &HarnessCommand::DraftSpec).await?;
                            state.mode = AppMode::DraftingSpec;
                        }
                        KeyCode::Char('r') if state.repo_focused => {
                            state.repo_mode = "variables".into();
                            mermaid.hide();
                        }
                        KeyCode::Char('t') if state.repo_focused => {
                            state.repo_mode = "files".into();
                            mermaid.hide();
                        }
                        KeyCode::Up if state.repo_focused => {
                            state.repo_selected = state.repo_selected.saturating_sub(1);
                        }
                        KeyCode::Down if state.repo_focused => {
                            let len = state.repo_files.len().max(state.repo_variables.len());
                            if state.repo_selected + 1 < len {
                                state.repo_selected += 1;
                            }
                        }
                        KeyCode::Char('r') if !state.running => {
                            send_command(
                                child_stdin,
                                &HarnessCommand::Run {
                                    entrypoint: "coding_capability".into(),
                                    args: vec!["--save-artifacts".into()],
                                },
                            ).await?;
                            state.running = true;
                        }
                        KeyCode::Char('c' | 'p')
                            if state.mode == AppMode::Chat && !state.assistant_busy =>
                        {
                            state.prompt_active = true;
                        }
                        KeyCode::Char('m') => {
                            state.repo_focused = true;
                            state.repo_mode = "diagram".into();
                            send_command(
                                child_stdin,
                                &HarnessCommand::RepoMap {
                                    root: repo_root.display().to_string(),
                                    focus: String::new(),
                                    mode: "diagram".into(),
                                },
                            ).await?;
                            send_command(
                                child_stdin,
                                &HarnessCommand::RepoMap {
                                    root: repo_root.display().to_string(),
                                    focus: String::new(),
                                    mode: "files".into(),
                                },
                            ).await?;
                            send_command(
                                child_stdin,
                                &HarnessCommand::RepoMap {
                                    root: repo_root.display().to_string(),
                                    focus: String::new(),
                                    mode: "variables".into(),
                                },
                            ).await?;
                            state.logs.push("building repository map…".into());
                        }
                        KeyCode::Char('d') => {
                            if state.history_visible {
                                state.history_visible = false;
                                state.history_detail = None;
                            } else {
                                send_command(
                                    child_stdin,
                                    &HarnessCommand::History {
                                        run_id: None,
                                        limit: None,
                                    },
                                ).await?;
                                state.logs.push("loading run history…".into());
                            }
                        }
                        KeyCode::Up if state.history_visible => {
                            state.history_selected = state.history_selected.saturating_sub(1);
                            state.history_detail = None;
                        }
                        KeyCode::Down if state.history_visible => {
                            if state.history_selected + 1 < state.history_runs.len() {
                                state.history_selected += 1;
                            }
                            state.history_detail = None;
                        }
                        KeyCode::Enter if state.history_visible => {
                            let run_id = state
                                .history_runs
                                .get(state.history_selected)
                                .map(|run| run.run_id.clone());
                            if let Some(run_id) = run_id {
                                send_command(
                                    child_stdin,
                                    &HarnessCommand::History {
                                        run_id: Some(run_id),
                                        limit: None,
                                    },
                                ).await?;
                            }
                        }
                        KeyCode::Esc if state.history_visible => {
                            state.history_visible = false;
                            state.history_detail = None;
                        }
                        KeyCode::Esc if state.repo_focused => {
                            state.repo_focused = false;
                            mermaid.hide();
                        }
                        _ => {}
                    }
                }
            }
            maybe_harness = rx.recv() => {
                if let Some(event) = maybe_harness {
                    let repo_map = match &event {
                        HarnessEvent::RepoMap { mermaid, .. } => Some(mermaid.clone()),
                        _ => None,
                    };
                    state.apply(event);
                    if let Some(source) = repo_map {
                        match mermaid.set_diagram(&source) {
                            Ok(()) => {
                                mermaid.show();
                                if mermaid.uses_text_fallback() {
                                    state.logs.push(
                                        "using the built-in quadrant-block renderer (2x2 pixels per terminal cell); iTerm2, WezTerm, Kitty, and Ghostty use native graphics".into()
                                    );
                                }
                            }
                            Err(error) => state.logs.push(format!("[diagram error] {error}")),
                        }
                    }
                }
            }
            _ = tick.tick() => {
                terminal.draw(|frame| draw(frame, &state, mermaid))?;
                mermaid.write_protocol(terminal.backend_mut())?;
            }
        }
    }
    Ok(())
}

async fn send_command(stdin: &mut ChildStdin, command: &HarnessCommand) -> Result<()> {
    let mut encoded = serde_json::to_vec(command)?;
    encoded.push(b'\n');
    stdin.write_all(&encoded).await?;
    stdin.flush().await?;
    Ok(())
}

fn draw(frame: &mut ratatui::Frame, state: &AppState, mermaid: &mut MermaidView) {
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(0), Constraint::Length(7)])
        .split(frame.area());
    let top =
        Layout::horizontal([Constraint::Percentage(75), Constraint::Percentage(25)]).split(rows[0]);
    let bottom = Layout::horizontal([
        Constraint::Percentage(20),
        Constraint::Percentage(55),
        Constraint::Percentage(25),
    ])
    .split(rows[1]);

    let items: Vec<ListItem> = state
        .logs
        .iter()
        .rev()
        .take(200)
        .rev()
        .map(|line| {
            let style = if line.starts_with("[error]") {
                Style::default()
                    .fg(Color::LightRed)
                    .add_modifier(Modifier::BOLD)
            } else if line.starts_with("[warning]") || line.starts_with("[protocol warning]") {
                Style::default()
                    .fg(Color::Yellow)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default()
            };
            ListItem::new(line.as_str()).style(style)
        })
        .collect();
    frame.render_widget(
        List::new(items).block(
            Block::default()
                .borders(Borders::ALL)
                .title(format!("main output · {} · {}%", state.engine, state.pct)),
        ),
        top[0],
    );
    let repo_title = format!(
        "repo map · {} · m focus · r variables · t files{}",
        state.repo_mode,
        if state.repo_focused { " · ACTIVE" } else { "" }
    );
    frame.render_widget(
        Paragraph::new(state.selected_repo_detail())
            .wrap(Wrap { trim: false })
            .block(Block::default().borders(Borders::ALL).title(repo_title)),
        top[1],
    );

    let context = format!(
        "mode: {} · status: {}\nDeepSeek: {} ({})\nmemory: {} preference(s)\n{}",
        state.mode.label(),
        if state.running { "running" } else { "idle" },
        if state.deepseek_configured {
            "configured"
        } else {
            "missing"
        },
        state.deepseek_source,
        state.preference_count,
        state.context_content
    );
    frame.render_widget(
        Paragraph::new(context).block(Block::default().borders(Borders::ALL).title("context")),
        bottom[0],
    );
    let prompt_title = if state.prompt_active {
        "Chat · Enter sends message only · Esc cancels input"
    } else if state.assistant_busy {
        "Chat · DeepSeek is responding"
    } else if state.mode == AppMode::Executing {
        "Execution · approved spec is running"
    } else if state.mode == AppMode::DraftingSpec {
        "Spec · drafting from conversation"
    } else if matches!(state.mode, AppMode::SpecReview { .. }) {
        "Spec review · y execute · n revise"
    } else {
        "Chat · c/p type · s draft spec · /remember <preference>"
    };
    let prompt_text = if state.prompt.is_empty() && !state.prompt_active {
        "Chat refines the idea without changing files. Execution requires a generated spec and explicit y approval."
    } else {
        state.prompt.as_str()
    };
    frame.render_widget(
        Paragraph::new(prompt_text)
            .block(Block::default().borders(Borders::ALL).title(prompt_title)),
        bottom[1],
    );
    frame.render_widget(
        Paragraph::new(format!(
            "Local memory\n{}\n{} preference(s)\n\nUse /remember <preference> in chat.",
            state.memory_path, state.preference_count
        ))
        .wrap(Wrap { trim: true })
        .block(Block::default().borders(Borders::ALL).title("settings")),
        bottom[2],
    );

    if mermaid.is_visible() {
        let area = mermaid.stabilize_viewport(centered_rect(86, 86, frame.area()), frame.area());
        frame.render_widget(Clear, area);
        let title = format!(
            "Repository diagram · {} · Esc/m to close",
            mermaid.status_label()
        );
        frame.render_widget(Block::default().borders(Borders::ALL).title(title), area);
        let inner = Rect {
            x: area.x + 1,
            y: area.y + 1,
            width: area.width.saturating_sub(2),
            height: area.height.saturating_sub(2),
        };
        if let Some(error) = mermaid.error() {
            frame.render_widget(Paragraph::new(error).wrap(Wrap { trim: true }), inner);
        } else {
            mermaid.render(frame, inner);
        }
    }

    if state.repo_focused && state.repo_mode != "diagram" {
        draw_repo_browser(frame, state);
    }

    if let AppMode::SpecReview { spec_text } = &state.mode {
        draw_spec_review(frame, spec_text);
    }

    if state.history_visible {
        let area = centered_rect(86, 86, frame.area());
        frame.render_widget(Clear, area);
        let title = if state.history_detail.is_some() {
            "Run history · detail · Esc/d to close"
        } else {
            "Run history · Up/Down select · Enter detail · Esc/d to close"
        };
        frame.render_widget(Block::default().borders(Borders::ALL).title(title), area);
        let inner = Rect {
            x: area.x + 1,
            y: area.y + 1,
            width: area.width.saturating_sub(2),
            height: area.height.saturating_sub(2),
        };
        if let Some(detail) = &state.history_detail {
            frame.render_widget(
                Paragraph::new(detail.as_str()).wrap(Wrap { trim: false }),
                inner,
            );
        } else if state.history_runs.is_empty() {
            frame.render_widget(
                Paragraph::new("no checkpointed runs found").wrap(Wrap { trim: true }),
                inner,
            );
        } else {
            let items: Vec<ListItem> = state
                .history_runs
                .iter()
                .enumerate()
                .map(|(index, run)| {
                    let marker = if index == state.history_selected {
                        "> "
                    } else {
                        "  "
                    };
                    ListItem::new(format!(
                        "{marker}{} · {} · {} · {} attempts",
                        run.run_id, run.target, run.final_status, run.attempt_count
                    ))
                })
                .collect();
            frame.render_widget(List::new(items), inner);
        }
    }
}

fn draw_spec_review(frame: &mut ratatui::Frame, spec_text: &str) {
    let area = centered_rect(90, 90, frame.area());
    frame.render_widget(Clear, area);
    frame.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .title("Spec draft ready · review carefully · y execute · n/Esc revise"),
        area,
    );
    let inner = Rect {
        x: area.x + 1,
        y: area.y + 1,
        width: area.width.saturating_sub(2),
        height: area.height.saturating_sub(2),
    };
    frame.render_widget(Paragraph::new(spec_text).wrap(Wrap { trim: false }), inner);
}

fn draw_repo_browser(frame: &mut ratatui::Frame, state: &AppState) {
    let area = centered_rect(86, 86, frame.area());
    frame.render_widget(Clear, area);
    frame.render_widget(
        Block::default()
            .borders(Borders::ALL)
            .title("Repository files · Up/Down select · t summary · r variables · Esc close"),
        area,
    );
    let inner = Rect {
        x: area.x + 1,
        y: area.y + 1,
        width: area.width.saturating_sub(2),
        height: area.height.saturating_sub(2),
    };
    let columns =
        Layout::horizontal([Constraint::Percentage(36), Constraint::Percentage(64)]).split(inner);
    let paths: Vec<ListItem> = state
        .repo_files
        .iter()
        .enumerate()
        .map(|(index, entry)| {
            let marker = if index == state.repo_selected {
                "> "
            } else {
                "  "
            };
            let style = if index == state.repo_selected {
                Style::default()
                    .fg(Color::Black)
                    .bg(Color::LightCyan)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default()
            };
            ListItem::new(format!("{marker}{}", entry.path)).style(style)
        })
        .collect();
    frame.render_widget(
        List::new(paths).block(Block::default().borders(Borders::RIGHT).title("files")),
        columns[0],
    );
    frame.render_widget(
        Paragraph::new(state.selected_repo_detail())
            .wrap(Wrap { trim: false })
            .block(Block::default().title(state.repo_mode.as_str())),
        columns[1],
    );
}

fn centered_rect(percent_x: u16, percent_y: u16, area: Rect) -> Rect {
    let vertical = Layout::vertical([
        Constraint::Percentage((100 - percent_y) / 2),
        Constraint::Percentage(percent_y),
        Constraint::Percentage((100 - percent_y) / 2),
    ])
    .split(area);
    Layout::horizontal([
        Constraint::Percentage((100 - percent_x) / 2),
        Constraint::Percentage(percent_x),
        Constraint::Percentage((100 - percent_x) / 2),
    ])
    .split(vertical[1])[1]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn app_state_transitions_are_pure() {
        let mut state = AppState::default();
        state.apply(HarnessEvent::EngineProgress {
            engine: "compile".into(),
            pct: 120,
        });
        assert_eq!(state.engine, "compile");
        assert_eq!(state.pct, 100);
        state.running = true;
        state.apply(HarnessEvent::Done {
            status: "completed".into(),
        });
        assert!(!state.running);
        assert_eq!(state.engine, "idle");
    }

    #[test]
    fn chat_spec_review_and_execution_are_distinct_states() {
        let mut state = AppState::default();
        assert_eq!(state.mode, AppMode::Chat);
        state.apply(HarnessEvent::AssistantStatus {
            stage: "drafting_spec".into(),
            busy: true,
        });
        state.mode = AppMode::DraftingSpec;
        assert!(state.assistant_busy);
        state.apply(HarnessEvent::SpecDraft {
            text: "# Approved candidate".into(),
        });
        assert_eq!(
            state.mode,
            AppMode::SpecReview {
                spec_text: "# Approved candidate".into()
            }
        );
        assert!(!state.running);
        state.mode = AppMode::Executing;
        state.running = true;
        state.apply(HarnessEvent::Done {
            status: "completed".into(),
        });
        assert_eq!(state.mode, AppMode::Chat);
        assert!(!state.running);
    }

    #[test]
    fn configuration_and_memory_status_are_visible_in_state() {
        let mut state = AppState::default();
        state.apply(HarnessEvent::ConfigStatus {
            deepseek_configured: true,
            source: ".env:DEEPSEEK_API_KEY".into(),
            memory_path: ".tui_memory.json".into(),
            preference_count: 3,
        });
        assert!(state.deepseek_configured);
        assert_eq!(state.preference_count, 3);
        assert!(state.logs.last().unwrap().contains("configured"));
        state.apply(HarnessEvent::MemoryUpdated {
            preference: "keep responses concise".into(),
            added: true,
            count: 4,
        });
        assert_eq!(state.preference_count, 4);
    }

    #[test]
    fn event_flood_is_bounded_without_losing_latest_messages() {
        let mut state = AppState::default();
        for index in 0..2_500 {
            state.apply(HarnessEvent::Log {
                level: "info".into(),
                msg: format!("event-{index}"),
            });
        }
        assert_eq!(state.logs.len(), 2_000);
        assert_eq!(state.logs.last().unwrap(), "[info] event-2499");
    }

    #[test]
    fn history_events_drive_modal_state() {
        let mut state = AppState::default();
        state.apply(HarnessEvent::HistoryList {
            runs: vec![
                RunSummary {
                    run_id: "run-a".into(),
                    target: "a.py".into(),
                    final_status: "accepted".into(),
                    attempt_count: 1,
                },
                RunSummary {
                    run_id: "run-b".into(),
                    target: "b.py".into(),
                    final_status: "manual_review_required".into(),
                    attempt_count: 3,
                },
            ],
        });
        assert!(state.history_visible);
        assert_eq!(state.history_runs.len(), 2);
        assert_eq!(state.history_selected, 0);
        assert!(state.history_detail.is_none());

        state.apply(HarnessEvent::HistoryDetail {
            run_id: "run-b".into(),
            checkpoint: std::collections::BTreeMap::new(),
        });
        assert!(state.history_visible);
        assert!(state
            .history_detail
            .as_deref()
            .unwrap()
            .contains("run run-b"));
    }

    #[test]
    fn structured_repo_entries_drive_local_file_details() {
        let mut state = AppState::default();
        state.apply(HarnessEvent::RepoMapFiles {
            entries: vec![
                FileEntry {
                    path: "a.py".into(),
                    summary: "First file".into(),
                    symbols: vec!["alpha".into()],
                },
                FileEntry {
                    path: "b.py".into(),
                    summary: "Second file".into(),
                    symbols: vec!["beta".into()],
                },
            ],
        });
        state.apply(HarnessEvent::RepoMapVariables {
            entries: vec![
                VariableEntry {
                    path: "a.py".into(),
                    imports: vec!["os".into()],
                    variables: vec!["ROOT".into()],
                },
                VariableEntry {
                    path: "b.py".into(),
                    imports: vec!["json".into()],
                    variables: vec!["DATA".into()],
                },
            ],
        });
        state.repo_selected = 1;
        state.repo_mode = "files".into();
        assert!(state.selected_repo_detail().contains("Second file"));
        state.repo_mode = "variables".into();
        let detail = state.selected_repo_detail();
        assert!(detail.contains("json"));
        assert!(detail.contains("DATA"));
    }
}
