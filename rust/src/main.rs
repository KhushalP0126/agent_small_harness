#[allow(dead_code)]
mod mermaid_view;
mod protocol;

use std::fs;
use std::io;
use std::panic;
use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;

use anyhow::{Context, Result};
use crossterm::{
    cursor::Show,
    event::{
        DisableMouseCapture, EnableMouseCapture, Event as CtEvent, EventStream, KeyCode,
        MouseEventKind,
    },
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use futures::{FutureExt, StreamExt};
use protocol::{
    read_harness_events, ClarificationQuestion, FileEntry, HarnessCommand, HarnessEvent,
    QuestionnaireAnswer, RunSummary, VariableEntry,
};
use ratatui::{
    backend::CrosstermBackend,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Modifier, Style},
    text::{Line, Span, Text},
    widgets::{Block, Borders, Clear, List, ListItem, Padding, Paragraph, Wrap},
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
    Questionnaire,
    DraftingSpec,
    SpecReview { spec_text: String },
    ToolDiffReview { path: String, diff: String },
    Executing,
}

impl AppMode {
    fn label(&self) -> &'static str {
        match self {
            Self::Chat => "chat",
            Self::Questionnaire => "questionnaire",
            Self::DraftingSpec => "drafting spec",
            Self::SpecReview { .. } => "spec review",
            Self::ToolDiffReview { .. } => "tool diff review",
            Self::Executing => "executing",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
struct AppState {
    mode: AppMode,
    logs: Vec<String>,
    log_scroll: usize,
    engine: String,
    pct: u16,
    running: bool,
    working_directory: String,
    repo_map_source: Option<String>,
    repo_map_url: Option<String>,
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
    tool_prompt_active: bool,
    assistant_busy: bool,
    numbered_options_available: bool,
    clarification_questions: Vec<ClarificationQuestion>,
    clarification_index: usize,
    clarification_answers: Vec<QuestionnaireAnswer>,
    questionnaire_other_active: bool,
    activity_tick: usize,
    deepseek_configured: bool,
    deepseek_source: String,
    memory_path: String,
    preference_count: u32,
    validated_source: Option<String>,
    validated_language: String,
    validated_artifact_path: String,
    validated_source_visible: bool,
    code_scroll_y: usize,
    code_scroll_x: usize,
    tool_diff_scroll: usize,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            mode: AppMode::Chat,
            logs: Vec::new(),
            log_scroll: 0,
            engine: "idle".into(),
            pct: 0,
            running: false,
            working_directory: String::new(),
            repo_map_source: None,
            repo_map_url: None,
            repo_content: "m map · r vars · t files".into(),
            repo_mode: "diagram".into(),
            repo_focused: false,
            repo_files: Vec::new(),
            repo_variables: Vec::new(),
            repo_selected: 0,
            context_content: "m map · d history".into(),
            history_visible: false,
            history_runs: Vec::new(),
            history_selected: 0,
            history_detail: None,
            prompt: String::new(),
            prompt_active: false,
            tool_prompt_active: false,
            assistant_busy: false,
            numbered_options_available: false,
            clarification_questions: Vec::new(),
            clarification_index: 0,
            clarification_answers: Vec::new(),
            questionnaire_other_active: false,
            activity_tick: 0,
            deepseek_configured: false,
            deepseek_source: "checking".into(),
            memory_path: ".tui_memory.json".into(),
            preference_count: 0,
            validated_source: None,
            validated_language: String::new(),
            validated_artifact_path: String::new(),
            validated_source_visible: false,
            code_scroll_y: 0,
            code_scroll_x: 0,
            tool_diff_scroll: 0,
        }
    }
}

impl AppState {
    fn context_remaining(&self) -> usize {
        const CONTEXT_BUDGET: usize = 8_192;
        let used = self
            .logs
            .iter()
            .map(|line| line.len())
            .sum::<usize>()
            .div_ceil(4);
        CONTEXT_BUDGET.saturating_sub(used)
    }

    fn persist_context(&self, repo_root: &std::path::Path) {
        let mut lines = vec![
            "# TUI Session Context".to_string(),
            "".to_string(),
            format!("Mode: {}", self.mode.label()),
            format!("Engine: {} · {}%", self.engine, self.pct),
            format!(
                "DeepSeek: {}",
                if self.deepseek_configured {
                    "configured"
                } else {
                    "not configured"
                }
            ),
            format!("Saved preferences: {}", self.preference_count),
            "".to_string(),
            "## Recent activity".to_string(),
        ];
        for entry in self.logs.iter().rev().take(24).rev() {
            lines.push(format!("- {}", redact_context(entry)));
        }
        lines.extend([
            "".to_string(),
            "This file is a local, ignored journal and is not sent to the model automatically."
                .to_string(),
        ]);
        let path = repo_root.join("context.md");
        let temporary = repo_root.join(".context.md.tmp");
        if fs::write(&temporary, format!("{}\n", lines.join("\n"))).is_ok() {
            let _ = fs::rename(temporary, path);
        }
    }

    fn apply(&mut self, event: HarnessEvent) {
        let previous_log_count = self.logs.len();
        match event {
            HarnessEvent::Ready { protocol_version } => {
                let _ = protocol_version;
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
            }
            HarnessEvent::AssistantStatus { stage, busy } => {
                self.assistant_busy = busy;
                self.engine = if busy { stage } else { "idle".into() };
            }
            HarnessEvent::ChatMessage { role, content } => {
                let label = if role == "user" { "you" } else { "assistant" };
                if role == "assistant" {
                    self.numbered_options_available = contains_numbered_options(&content);
                } else {
                    self.numbered_options_available = false;
                }
                self.logs.push(format!("[{label}] {content}"));
            }
            HarnessEvent::Questionnaire { questions } => {
                if questions.is_empty() {
                    self.logs
                        .push("[protocol warning] questionnaire contained no questions".into());
                    return;
                }
                self.clarification_questions = questions;
                self.clarification_index = 0;
                self.clarification_answers.clear();
                self.questionnaire_other_active = false;
                self.numbered_options_available = false;
                self.mode = AppMode::Questionnaire;
                self.logs.push(format!(
                    "[assistant] questionnaire ready · {} clarification question(s)",
                    self.clarification_questions.len()
                ));
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
                self.clarification_questions.clear();
                self.clarification_answers.clear();
                self.questionnaire_other_active = false;
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
            HarnessEvent::RepoMapUrl { url } => {
                self.repo_map_url = Some(url.clone());
                self.repo_focused = true;
                self.logs.push(format!("repository map ready · press o to open {url}"));
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
            HarnessEvent::ValidatedSource {
                language,
                source,
                artifact_path,
            } => {
                self.validated_language = language;
                self.validated_source = Some(source);
                self.validated_artifact_path = artifact_path;
                self.validated_source_visible = true;
                self.code_scroll_y = 0;
                self.code_scroll_x = 0;
                self.logs
                    .push("validated source ready · press v to view it again".into());
            }
            HarnessEvent::ToolCall {
                turn,
                tool,
                ok,
                summary,
            } => self.logs.push(format!(
                "[tool {turn}] {tool}: {}{}",
                if ok { "completed" } else { "failed" },
                if summary.is_empty() {
                    String::new()
                } else {
                    format!(" · {summary}")
                }
            )),
            HarnessEvent::ToolAnswer {
                answer,
                exhausted,
                call_count,
            } => self.logs.push(format!(
                "[assistant] repository task {} after {call_count} tool call(s): {answer}",
                if exhausted { "stopped at its limit" } else { "finished" }
            )),
            HarnessEvent::ToolDiff {
                path,
                diff,
                replacements,
            } => {
                self.logs.push(format!(
                    "tool proposed {replacements} replacement(s) in {path}; review with y/n"
                ));
                self.tool_diff_scroll = 0;
                self.mode = AppMode::ToolDiffReview { path, diff };
            }
            HarnessEvent::ToolDiffResolved {
                path,
                applied,
                message,
            } => {
                self.logs.push(format!(
                    "tool diff {path}: {} · {message}",
                    if applied { "applied" } else { "not applied" }
                ));
                self.mode = AppMode::Chat;
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
        if self.log_scroll > 0 {
            self.log_scroll = self
                .log_scroll
                .saturating_add(self.logs.len().saturating_sub(previous_log_count));
        }
        const MAX_LOGS: usize = 2_000;
        if self.logs.len() > MAX_LOGS {
            self.logs.drain(..self.logs.len() - MAX_LOGS);
        }
    }

    fn main_output_active(&self) -> bool {
        !self.prompt_active
            && !self.repo_focused
            && !self.history_visible
            && !self.validated_source_visible
            && !matches!(
                self.mode,
                AppMode::Questionnaire
                    | AppMode::SpecReview { .. }
                    | AppMode::ToolDiffReview { .. }
            )
    }

    fn scroll_logs_up(&mut self, amount: usize) {
        self.log_scroll = self.log_scroll.saturating_add(amount);
    }

    fn scroll_logs_down(&mut self, amount: usize) {
        self.log_scroll = self.log_scroll.saturating_sub(amount);
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

    fn select_numbered_option(&mut self, digit: char) -> bool {
        if !self.numbered_options_available
            || self.mode != AppMode::Chat
            || self.assistant_busy
            || !(('1'..='5').contains(&digit))
        {
            return false;
        }
        self.prompt = format!("{digit}. ");
        self.prompt_active = true;
        true
    }

    fn choose_questionnaire_option(&mut self, digit: char) -> QuestionnaireAction {
        if self.mode != AppMode::Questionnaire || self.assistant_busy {
            return QuestionnaireAction::Ignored;
        }
        let Some(question) = self.clarification_questions.get(self.clarification_index) else {
            return QuestionnaireAction::Ignored;
        };
        let Some(option) = question
            .options
            .iter()
            .find(|option| char::from_digit(u32::from(option.id), 10) == Some(digit))
        else {
            return QuestionnaireAction::Ignored;
        };
        if option.text.eq_ignore_ascii_case("other") {
            self.prompt.clear();
            self.prompt_active = true;
            self.questionnaire_other_active = true;
            return QuestionnaireAction::AwaitingOther;
        }
        self.record_questionnaire_answer(option.text.clone())
    }

    fn record_questionnaire_answer(&mut self, answer: String) -> QuestionnaireAction {
        let Some(question) = self.clarification_questions.get(self.clarification_index) else {
            return QuestionnaireAction::Ignored;
        };
        self.clarification_answers.push(QuestionnaireAnswer {
            question_text: question.question_text.clone(),
            answer,
        });
        self.clarification_index += 1;
        self.questionnaire_other_active = false;
        self.prompt_active = false;
        self.prompt.clear();
        if self.clarification_index >= self.clarification_questions.len() {
            QuestionnaireAction::Complete(self.clarification_answers.clone())
        } else {
            QuestionnaireAction::Advanced
        }
    }
}

fn redact_context(value: &str) -> String {
    let mut text = value.replace('\n', " ");
    for marker in ["DEEPSEEK_API_KEY", "ARCHITECT_API_KEY"] {
        if let Some(index) = text.find(marker) {
            if let Some(equal) = text[index..].find('=') {
                let start = index + equal + 1;
                let end = text[start..]
                    .find(char::is_whitespace)
                    .map(|offset| start + offset)
                    .unwrap_or(text.len());
                text.replace_range(start..end, "[redacted]");
            }
        }
    }
    if text.len() > 500 {
        text.truncate(500);
        text.push('…');
    }
    text
}

#[derive(Debug, Clone, PartialEq)]
enum QuestionnaireAction {
    Ignored,
    AwaitingOther,
    Advanced,
    Complete(Vec<QuestionnaireAnswer>),
}

fn contains_numbered_options(content: &str) -> bool {
    let mut seen = [false; 5];
    for line in content.lines() {
        let mut chars = line.trim_start().chars();
        let Some(digit @ '1'..='5') = chars.next() else {
            continue;
        };
        if !matches!(chars.next(), Some('.' | ')' | ':')) {
            continue;
        }
        seen[(digit as u8 - b'1') as usize] = true;
    }
    seen.into_iter().filter(|present| *present).count() >= 2
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
    run_loop(&mut terminal, child_stdin, rx, repo_root).await
}

async fn run_loop(
    terminal: &mut Terminal<CrosstermBackend<io::Stdout>>,
    child_stdin: &mut ChildStdin,
    rx: &mut mpsc::UnboundedReceiver<HarnessEvent>,
    repo_root: &std::path::Path,
) -> Result<()> {
    let mut state = AppState::default();
    state.working_directory = repo_root.display().to_string();
    state.persist_context(repo_root);
    let mut term_events = EventStream::new();
    let mut tick = tokio::time::interval(Duration::from_millis(33));
    loop {
        tokio::select! {
            maybe_event = term_events.next().fuse() => {
                if let Some(Ok(event)) = maybe_event {
                    if let CtEvent::Mouse(mouse) = event {
                        match mouse.kind {
                            MouseEventKind::ScrollUp if state.validated_source_visible => {
                                state.code_scroll_y = state.code_scroll_y.saturating_sub(3);
                            }
                            MouseEventKind::ScrollDown if state.validated_source_visible => {
                                state.code_scroll_y = state.code_scroll_y.saturating_add(3);
                            }
                            MouseEventKind::ScrollUp if state.main_output_active() => {
                                state.scroll_logs_up(3);
                            }
                            MouseEventKind::ScrollDown if state.main_output_active() => {
                                state.scroll_logs_down(3);
                            }
                            _ => {}
                        }
                        continue;
                    }
                    let CtEvent::Key(key) = event else {
                        continue;
                    };
                    if state.prompt_active {
                        match key.code {
                            KeyCode::Esc => {
                                state.prompt_active = false;
                                state.tool_prompt_active = false;
                                state.questionnaire_other_active = false;
                                state.prompt.clear();
                            }
                            KeyCode::Backspace => {
                                state.prompt.pop();
                            }
                            KeyCode::Enter
                                if state.questionnaire_other_active
                                    && !state.prompt.trim().is_empty() =>
                            {
                                let answer = state.prompt.trim().to_owned();
                                if let QuestionnaireAction::Complete(answers) =
                                    state.record_questionnaire_answer(answer)
                                {
                                    send_command(
                                        child_stdin,
                                        &HarnessCommand::QuestionnaireComplete { answers },
                                    )
                                    .await?;
                                    state.mode = AppMode::DraftingSpec;
                                }
                            }
                            KeyCode::Enter if !state.prompt.trim().is_empty() && !state.assistant_busy => {
                                let text = std::mem::take(&mut state.prompt);
                                if state.tool_prompt_active {
                                    send_command(
                                        child_stdin,
                                        &HarnessCommand::ToolTask {
                                            text,
                                            provider: "qwen".into(),
                                        },
                                    )
                                    .await?;
                                    state.logs.push("repository tool task started…".into());
                                } else {
                                    send_command(child_stdin, &HarnessCommand::Chat { text }).await?;
                                }
                                state.prompt_active = false;
                                state.tool_prompt_active = false;
                            }
                            KeyCode::Char(digit @ '1'..='5')
                                if state.prompt.is_empty()
                                    && state.select_numbered_option(digit) => {}
                            KeyCode::Char(character) => state.prompt.push(character),
                            _ => {}
                        }
                        continue;
                    }
                    if state.validated_source_visible {
                        match key.code {
                            KeyCode::Esc | KeyCode::Char('v') => {
                                state.validated_source_visible = false;
                            }
                            KeyCode::Up => {
                                state.code_scroll_y = state.code_scroll_y.saturating_sub(1);
                            }
                            KeyCode::Down => {
                                state.code_scroll_y = state.code_scroll_y.saturating_add(1);
                            }
                            KeyCode::PageUp => {
                                state.code_scroll_y = state.code_scroll_y.saturating_sub(20);
                            }
                            KeyCode::PageDown => {
                                state.code_scroll_y = state.code_scroll_y.saturating_add(20);
                            }
                            KeyCode::Left => {
                                state.code_scroll_x = state.code_scroll_x.saturating_sub(4);
                            }
                            KeyCode::Right => {
                                state.code_scroll_x = state.code_scroll_x.saturating_add(4);
                            }
                            KeyCode::Home => {
                                state.code_scroll_y = 0;
                                state.code_scroll_x = 0;
                            }
                            _ => {}
                        }
                        continue;
                    }
                    match key.code {
                        KeyCode::Char(digit @ '1'..='5')
                            if state.mode == AppMode::Questionnaire =>
                        {
                            if let QuestionnaireAction::Complete(answers) =
                                state.choose_questionnaire_option(digit)
                            {
                                send_command(
                                    child_stdin,
                                    &HarnessCommand::QuestionnaireComplete { answers },
                                )
                                .await?;
                                state.mode = AppMode::DraftingSpec;
                            }
                        }
                        KeyCode::Esc if state.mode == AppMode::Questionnaire => {
                            state.mode = AppMode::Chat;
                            state.clarification_questions.clear();
                            state.clarification_answers.clear();
                            state.logs.push(
                                "questionnaire cancelled; continue refining the idea in chat"
                                    .into(),
                            );
                        }
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
                            state.validated_source = None;
                            state.validated_source_visible = false;
                            state.code_scroll_y = 0;
                            state.code_scroll_x = 0;
                            state
                                .logs
                                .push("approved spec sent to the execution pipeline".into());
                        }
                        KeyCode::Char('y')
                            if matches!(state.mode, AppMode::ToolDiffReview { .. }) =>
                        {
                            send_command(
                                child_stdin,
                                &HarnessCommand::ApplyToolDiff { approved: true },
                            )
                            .await?;
                            state.logs.push("approved tool diff; verifying and applying…".into());
                        }
                        KeyCode::Char('n') | KeyCode::Esc
                            if matches!(state.mode, AppMode::ToolDiffReview { .. }) =>
                        {
                            send_command(
                                child_stdin,
                                &HarnessCommand::ApplyToolDiff { approved: false },
                            )
                            .await?;
                        }
                        KeyCode::Up if matches!(state.mode, AppMode::ToolDiffReview { .. }) => {
                            state.tool_diff_scroll = state.tool_diff_scroll.saturating_sub(1);
                        }
                        KeyCode::Down if matches!(state.mode, AppMode::ToolDiffReview { .. }) => {
                            state.tool_diff_scroll = state.tool_diff_scroll.saturating_add(1);
                        }
                        KeyCode::PageUp
                            if matches!(state.mode, AppMode::ToolDiffReview { .. }) =>
                        {
                            state.tool_diff_scroll = state.tool_diff_scroll.saturating_sub(20);
                        }
                        KeyCode::PageDown
                            if matches!(state.mode, AppMode::ToolDiffReview { .. }) =>
                        {
                            state.tool_diff_scroll = state.tool_diff_scroll.saturating_add(20);
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
                            state.tool_prompt_active = false;
                        }
                        KeyCode::Char('a')
                            if state.mode == AppMode::Chat && !state.assistant_busy =>
                        {
                            state.prompt.clear();
                            state.prompt_active = true;
                            state.tool_prompt_active = true;
                        }
                        KeyCode::Char(digit @ '1'..='5')
                            if state.select_numbered_option(digit) => {}
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
                            state.logs.push("building browser map…".into());
                        }
                        KeyCode::Char('o') if state.repo_focused => {
                            if let Some(url) = state.repo_map_url.as_deref() {
                                open_localhost(url);
                            } else {
                                state.logs.push("map is not ready yet; press m first".into());
                            }
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
                        KeyCode::Char('v') if state.validated_source.is_some() => {
                            state.validated_source_visible = true;
                            state.code_scroll_y = 0;
                            state.code_scroll_x = 0;
                        }
                        KeyCode::Up if state.main_output_active() => {
                            state.scroll_logs_up(1);
                        }
                        KeyCode::Down if state.main_output_active() => {
                            state.scroll_logs_down(1);
                        }
                        KeyCode::PageUp if state.main_output_active() => {
                            state.scroll_logs_up(20);
                        }
                        KeyCode::PageDown if state.main_output_active() => {
                            state.scroll_logs_down(20);
                        }
                        KeyCode::Home if state.main_output_active() => {
                            state.log_scroll = usize::MAX;
                        }
                        KeyCode::End if state.main_output_active() => {
                            state.log_scroll = 0;
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
                        }
                        _ => {}
                    }
                }
            }
            maybe_harness = rx.recv() => {
                if let Some(event) = maybe_harness {
                    state.apply(event);
                    state.persist_context(repo_root);
                }
            }
            _ = tick.tick() => {
                state.activity_tick = state.activity_tick.wrapping_add(1);
            terminal.draw(|frame| draw(frame, &state))?;
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

fn open_localhost(url: &str) {
    let (program, args): (&str, [&str; 1]) = if cfg!(target_os = "macos") {
        ("open", [url])
    } else if cfg!(target_os = "windows") {
        ("cmd", [url])
    } else {
        ("xdg-open", [url])
    };
    let _ = std::process::Command::new(program).args(args).spawn();
}

fn draw(frame: &mut ratatui::Frame, state: &AppState) {
    frame.render_widget(
        Block::default().style(Style::default().bg(theme_background())),
        frame.area(),
    );
    let rows = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1),
            Constraint::Min(0),
            Constraint::Length(6),
        ])
        .split(frame.area());
    let top = Layout::horizontal([
        Constraint::Percentage(74),
        Constraint::Length(1),
        Constraint::Min(0),
    ])
    .split(rows[1]);
    let bottom = Layout::horizontal([
        Constraint::Percentage(22),
        Constraint::Length(1),
        Constraint::Percentage(53),
        Constraint::Length(1),
        Constraint::Min(0),
    ])
    .split(rows[2]);

    frame.render_widget(activity_status_line(state), rows[0]);

    let mut log_lines: Vec<Line> = Vec::new();
    let mut previous_role: Option<&str> = None;
    for line in &state.logs {
        let role = log_role(line);
        if role.is_some() && role != previous_role && !log_lines.is_empty() {
            log_lines.push(Line::default());
        }
        log_lines.extend(styled_log_lines(line));
        previous_role = role;
    }
    let viewport_height = usize::from(top[0].height.saturating_sub(2));
    let max_log_offset = log_lines.len().saturating_sub(viewport_height);
    let from_bottom = state.log_scroll.min(max_log_offset);
    let log_offset = max_log_offset
        .saturating_sub(from_bottom)
        .min(u16::MAX as usize) as u16;
    let main_active = state.main_output_active();
    let scroll_status = if from_bottom == 0 {
        "follow".to_owned()
    } else {
        format!("{from_bottom} line(s) back")
    };
    frame.render_widget(
        Paragraph::new(Text::from(log_lines))
            .style(Style::default().fg(theme_foreground()))
            .wrap(Wrap { trim: false })
            .scroll((log_offset, 0))
            .block(pane_block(
                format!(" OUTPUT · {} · {scroll_status} ", state.engine),
                main_active,
            )),
        top[0],
    );
    let workspace = format!("{}\n\n[m] map · [o] browser", state.working_directory);
    frame.render_widget(
        Paragraph::new(workspace)
            .style(Style::default().fg(theme_foreground()))
            .wrap(Wrap { trim: false })
            .block(pane_block(" WORKSPACE ", false)),
        top[2],
    );

    let context = format!(
        "{} · {}\n{} prefs\n~{} ctx left",
        state.mode.label(),
        if state.deepseek_configured {
            "DeepSeek"
        } else {
            "no key"
        },
        state.preference_count,
        state.context_remaining(),
    );
    frame.render_widget(
        Paragraph::new(context)
            .style(Style::default().fg(theme_foreground()))
            .block(pane_block_accent(" CONTEXT ", false, theme_context())),
        bottom[0],
    );
    let prompt_title = if state.questionnaire_other_active {
        " ANSWER · Enter "
    } else if state.tool_prompt_active {
        " TOOLS · Enter "
    } else if state.prompt_active {
        " CHAT · Enter "
    } else if state.assistant_busy {
        " CHAT · thinking "
    } else if state.mode == AppMode::Executing {
        " RUNNING "
    } else if state.mode == AppMode::DraftingSpec {
        " DRAFTING "
    } else if matches!(state.mode, AppMode::SpecReview { .. }) {
        " REVIEW · y/n "
    } else if matches!(state.mode, AppMode::ToolDiffReview { .. }) {
        " DIFF · y/n "
    } else {
        " CHAT "
    };
    let prompt_text = if state.prompt.is_empty() && !state.prompt_active {
        Text::from(Line::from(vec![
            Span::styled("  ", Style::default().bg(Color::LightCyan)),
            Span::raw("  c chat · s spec · a tools · d history"),
        ]))
    } else {
        Text::from(state.prompt.as_str())
    };
    frame.render_widget(
        Paragraph::new(prompt_text)
            .style(Style::default().fg(theme_foreground()))
            .block(pane_block(prompt_title, state.prompt_active)),
        bottom[2],
    );
    frame.render_widget(
        Paragraph::new(format!("memory · {} saved", state.preference_count))
            .style(Style::default().fg(theme_foreground()))
            .wrap(Wrap { trim: true })
            .block(pane_block_accent(" SETTINGS ", false, theme_settings())),
        bottom[4],
    );

    if let AppMode::SpecReview { spec_text } = &state.mode {
        draw_spec_review(frame, spec_text);
    }

    if matches!(state.mode, AppMode::ToolDiffReview { .. }) {
        draw_tool_diff_review(frame, state);
    }

    if state.mode == AppMode::Questionnaire {
        draw_questionnaire(frame, state);
    }

    if state.history_visible {
        let area = centered_rect(86, 86, frame.area());
        frame.render_widget(Clear, area);
        let title = if state.history_detail.is_some() {
            "Run history · detail · Esc/d to close"
        } else {
            "Run history · Up/Down select · Enter detail · Esc/d to close"
        };
        frame.render_widget(pane_block(title, true), area);
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

    if state.validated_source_visible {
        draw_validated_source(frame, state);
    }
}

fn draw_questionnaire(frame: &mut ratatui::Frame, state: &AppState) {
    let area = centered_rect(78, 72, frame.area());
    frame.render_widget(Clear, area);
    let total = state.clarification_questions.len();
    let current = state.clarification_index.saturating_add(1).min(total);
    frame.render_widget(
        pane_block(
            format!(" Clarify the project · question {current}/{total} · 1-5 choose · Esc cancel "),
            true,
        ),
        area,
    );
    let inner = Rect {
        x: area.x + 2,
        y: area.y + 2,
        width: area.width.saturating_sub(4),
        height: area.height.saturating_sub(4),
    };
    let Some(question) = state.clarification_questions.get(state.clarification_index) else {
        frame.render_widget(
            Paragraph::new("No clarification question available."),
            inner,
        );
        return;
    };
    let mut lines = vec![
        Line::styled(
            question.question_text.clone(),
            Style::default()
                .fg(Color::White)
                .add_modifier(Modifier::BOLD),
        ),
        Line::raw(""),
    ];
    for option in &question.options {
        lines.push(Line::from(vec![
            Span::styled(
                format!(" {} ", option.id),
                Style::default()
                    .fg(Color::Black)
                    .bg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                format!("  {}", option.text),
                Style::default().fg(if option.text.eq_ignore_ascii_case("other") {
                    Color::Magenta
                } else {
                    Color::White
                }),
            ),
        ]));
        lines.push(Line::raw(""));
    }
    lines.push(Line::styled(
        format!(
            "{} answer(s) recorded · the final answer drafts a spec for review",
            state.clarification_answers.len()
        ),
        Style::default().fg(Color::DarkGray),
    ));
    frame.render_widget(Paragraph::new(lines).wrap(Wrap { trim: false }), inner);
}

fn activity_status_line(state: &AppState) -> Paragraph<'static> {
    let busy = state.assistant_busy || state.running;
    let spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
    let marker = if busy {
        spinner[(state.activity_tick / 3) % spinner.len()]
    } else {
        "●"
    };
    let status = if state.assistant_busy {
        state.engine.clone()
    } else if state.running {
        format!("{} · {}%", state.engine, state.pct)
    } else {
        "ready".into()
    };
    Paragraph::new(Line::from(vec![
        Span::styled(
            format!(" {marker} "),
            Style::default()
                .fg(if busy { Color::Black } else { Color::Cyan })
                .bg(if busy { Color::Cyan } else { Color::Reset })
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled(
            format!(" {status} "),
            Style::default().fg(if busy { Color::LightCyan } else { Color::Gray }),
        ),
    ]))
    .style(Style::default().bg(theme_background()).fg(theme_muted()))
}

fn theme_background() -> Color {
    Color::Rgb(9, 13, 20)
}

fn theme_panel() -> Color {
    Color::Rgb(15, 23, 34)
}

fn theme_foreground() -> Color {
    Color::Rgb(226, 232, 240)
}

fn theme_muted() -> Color {
    Color::Rgb(148, 163, 184)
}

fn theme_context() -> Color {
    Color::Rgb(96, 165, 250)
}

fn theme_settings() -> Color {
    Color::Rgb(192, 132, 252)
}

fn pane_block<'a>(title: impl Into<Line<'a>>, active: bool) -> Block<'a> {
    pane_block_accent(title, active, Color::Rgb(71, 85, 105))
}

fn pane_block_accent<'a>(title: impl Into<Line<'a>>, active: bool, accent: Color) -> Block<'a> {
    let color = if active {
        Color::Rgb(45, 212, 191)
    } else {
        accent
    };
    Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(color))
        .style(Style::default().bg(theme_panel()))
        .padding(Padding::horizontal(1))
        .title(title)
        .title_style(Style::default().fg(color).add_modifier(Modifier::BOLD))
}

fn styled_log_lines(line: &str) -> Vec<Line<'static>> {
    let role = [
        ("[you] ", Color::Green, Color::White),
        ("[assistant] ", Color::Cyan, Color::LightCyan),
        ("[memory] ", Color::Magenta, Color::White),
        ("[error] ", Color::LightRed, Color::LightRed),
        ("[warning] ", Color::Yellow, Color::Yellow),
        ("[protocol warning] ", Color::Yellow, Color::Yellow),
    ]
    .into_iter()
    .find(|(prefix, _, _)| line.starts_with(prefix));

    let Some((prefix, prefix_color, content_color)) = role else {
        return line
            .lines()
            .map(|value| Line::raw(value.to_owned()))
            .collect();
    };
    let content = &line[prefix.len()..];
    let mut source_lines = content.lines();
    let first = source_lines.next().unwrap_or_default();
    let mut rendered = vec![Line::from(vec![
        Span::styled(
            prefix.to_owned(),
            Style::default()
                .fg(prefix_color)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled(first.to_owned(), Style::default().fg(content_color)),
    ])];
    let indent = " ".repeat(prefix.chars().count());
    rendered.extend(source_lines.map(|continuation| {
        Line::from(vec![
            Span::raw(indent.clone()),
            Span::styled(continuation.to_owned(), Style::default().fg(content_color)),
        ])
    }));
    rendered
}

fn log_role(line: &str) -> Option<&'static str> {
    [
        "[you] ",
        "[assistant] ",
        "[memory] ",
        "[error] ",
        "[warning] ",
        "[protocol warning] ",
    ]
    .into_iter()
    .find(|prefix| line.starts_with(prefix))
}

fn draw_validated_source(frame: &mut ratatui::Frame, state: &AppState) {
    let Some(source) = state.validated_source.as_deref() else {
        return;
    };
    let area = centered_rect(92, 90, frame.area());
    frame.render_widget(Clear, area);
    let artifact = if state.validated_artifact_path.is_empty() {
        String::new()
    } else {
        format!(" · {}", state.validated_artifact_path)
    };
    frame.render_widget(
        pane_block(
            format!(
                " Validated code · {}{artifact} · arrows/PgUp/PgDn scroll · v/Esc close ",
                state.validated_language
            ),
            true,
        ),
        area,
    );
    let inner = Rect {
        x: area.x + 1,
        y: area.y + 1,
        width: area.width.saturating_sub(2),
        height: area.height.saturating_sub(2),
    };
    let source_lines: Vec<Line> = source
        .lines()
        .enumerate()
        .map(|(index, line)| {
            Line::from(vec![
                Span::styled(
                    format!("{:>4} ", index + 1),
                    Style::default().fg(Color::DarkGray),
                ),
                Span::styled(line.to_owned(), Style::default().fg(Color::White)),
            ])
        })
        .collect();
    let max_y = source_lines.len().saturating_sub(usize::from(inner.height));
    let scroll_y = state.code_scroll_y.min(max_y).min(u16::MAX as usize) as u16;
    let scroll_x = state.code_scroll_x.min(u16::MAX as usize) as u16;
    frame.render_widget(
        Paragraph::new(Text::from(source_lines)).scroll((scroll_y, scroll_x)),
        inner,
    );
}

fn draw_spec_review(frame: &mut ratatui::Frame, spec_text: &str) {
    let area = centered_rect(90, 90, frame.area());
    frame.render_widget(Clear, area);
    frame.render_widget(
        pane_block(
            " Spec draft ready · review carefully · y execute · n/Esc revise ",
            true,
        ),
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

fn draw_tool_diff_review(frame: &mut ratatui::Frame, state: &AppState) {
    let AppMode::ToolDiffReview { path, diff } = &state.mode else {
        return;
    };
    let area = centered_rect(92, 90, frame.area());
    frame.render_widget(Clear, area);
    frame.render_widget(
        pane_block(
            format!(" Tool diff · {path} · y apply · n/Esc discard · Up/Down/PgUp/PgDn "),
            true,
        ),
        area,
    );
    let inner = Rect {
        x: area.x + 1,
        y: area.y + 1,
        width: area.width.saturating_sub(2),
        height: area.height.saturating_sub(2),
    };
    let lines: Vec<Line> = diff
        .lines()
        .map(|line| {
            let color = if line.starts_with("+++") || line.starts_with("---") {
                Color::Cyan
            } else if line.starts_with('+') {
                Color::Green
            } else if line.starts_with('-') {
                Color::Red
            } else if line.starts_with("@@") {
                Color::Yellow
            } else {
                Color::White
            };
            Line::styled(line.to_owned(), Style::default().fg(color))
        })
        .collect();
    let max_scroll = lines.len().saturating_sub(usize::from(inner.height));
    let scroll = state
        .tool_diff_scroll
        .min(max_scroll)
        .min(u16::MAX as usize) as u16;
    frame.render_widget(
        Paragraph::new(Text::from(lines))
            .scroll((scroll, 0))
            .wrap(Wrap { trim: false }),
        inner,
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
    fn validated_source_opens_a_reusable_code_view() {
        let mut state = AppState::default();
        state.apply(HarnessEvent::ValidatedSource {
            language: "python".into(),
            source: "def main():\n    return 0\n".into(),
            artifact_path: "artifacts/runs/example".into(),
        });

        assert!(state.validated_source_visible);
        assert_eq!(
            state.validated_source.as_deref(),
            Some("def main():\n    return 0\n")
        );
        assert_eq!(state.validated_language, "python");
        assert!(state.logs.last().unwrap().contains("press v"));
    }

    #[test]
    fn tool_diff_opens_review_and_resolution_returns_to_chat() {
        let mut state = AppState::default();
        state.apply(HarnessEvent::ToolDiff {
            path: "rust/src/main.rs".into(),
            diff: "--- a/rust/src/main.rs\n+++ b/rust/src/main.rs\n".into(),
            replacements: 1,
        });
        assert_eq!(
            state.mode,
            AppMode::ToolDiffReview {
                path: "rust/src/main.rs".into(),
                diff: "--- a/rust/src/main.rs\n+++ b/rust/src/main.rs\n".into(),
            }
        );
        assert!(!state.main_output_active());

        state.apply(HarnessEvent::ToolDiffResolved {
            path: "rust/src/main.rs".into(),
            applied: false,
            message: "diff discarded".into(),
        });
        assert_eq!(state.mode, AppMode::Chat);
    }

    #[test]
    fn output_scroll_is_saturating_and_new_logs_preserve_history_position() {
        let mut state = AppState::default();
        state.scroll_logs_up(12);
        state.apply(HarnessEvent::Log {
            level: "info".into(),
            msg: "new output".into(),
        });
        assert_eq!(state.log_scroll, 13);
        state.scroll_logs_down(50);
        assert_eq!(state.log_scroll, 0);
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
        state.apply(HarnessEvent::MemoryUpdated {
            preference: "keep responses concise".into(),
            added: true,
            count: 4,
        });
        assert_eq!(state.preference_count, 4);
    }

    #[test]
    fn numbered_assistant_choices_enable_quick_selection() {
        let mut state = AppState::default();
        state.apply(HarnessEvent::ChatMessage {
            role: "assistant".into(),
            content: "Choose one:\n1. Parser\n2. Compiler\n3. Both".into(),
        });
        assert!(state.numbered_options_available);
        assert!(state.select_numbered_option('2'));
        assert!(state.prompt_active);
        assert_eq!(state.prompt, "2. ");

        state.apply(HarnessEvent::ChatMessage {
            role: "user".into(),
            content: "2. Compiler".into(),
        });
        assert!(!state.numbered_options_available);
    }

    #[test]
    fn ordinary_numbers_do_not_enable_quick_selection() {
        let mut state = AppState::default();
        state.apply(HarnessEvent::ChatMessage {
            role: "assistant".into(),
            content: "The 2026 build has 5 modules.".into(),
        });
        assert!(!state.numbered_options_available);
        assert!(!state.select_numbered_option('5'));
        assert!(!state.prompt_active);
    }

    #[test]
    fn questionnaire_collects_choices_and_other_before_drafting() {
        let mut state = AppState::default();
        state.apply(HarnessEvent::Questionnaire {
            questions: vec![
                ClarificationQuestion {
                    question_text: "Choose a surface".into(),
                    options: vec![
                        protocol::QuestionOption {
                            id: 1,
                            text: "CLI".into(),
                        },
                        protocol::QuestionOption {
                            id: 2,
                            text: "Other".into(),
                        },
                    ],
                },
                ClarificationQuestion {
                    question_text: "Choose storage".into(),
                    options: vec![
                        protocol::QuestionOption {
                            id: 1,
                            text: "JSON".into(),
                        },
                        protocol::QuestionOption {
                            id: 2,
                            text: "Other".into(),
                        },
                    ],
                },
            ],
        });

        assert_eq!(state.mode, AppMode::Questionnaire);
        assert_eq!(
            state.choose_questionnaire_option('1'),
            QuestionnaireAction::Advanced
        );
        assert_eq!(state.clarification_index, 1);
        assert_eq!(
            state.choose_questionnaire_option('2'),
            QuestionnaireAction::AwaitingOther
        );
        assert!(state.questionnaire_other_active);
        let QuestionnaireAction::Complete(answers) =
            state.record_questionnaire_answer("SQLite".into())
        else {
            panic!("final questionnaire answer should complete the questionnaire");
        };
        assert_eq!(answers.len(), 2);
        assert_eq!(answers[0].answer, "CLI");
        assert_eq!(answers[1].answer, "SQLite");
        assert!(!state.prompt_active);
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
