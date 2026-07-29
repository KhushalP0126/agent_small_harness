mod mermaid_view;
mod protocol;

use std::io;
use std::path::PathBuf;
use std::process::Stdio;
use std::time::Duration;

use anyhow::{Context, Result};
use crossterm::{
    event::{DisableMouseCapture, EnableMouseCapture, Event as CtEvent, EventStream, KeyCode},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use futures::{FutureExt, StreamExt};
use mermaid_view::MermaidView;
use protocol::{read_harness_events, HarnessCommand, HarnessEvent};
use ratatui::{
    backend::CrosstermBackend,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Style},
    widgets::{Block, Borders, Clear, Gauge, List, ListItem, Paragraph, Wrap},
    Terminal,
};
use tokio::{
    io::{AsyncWriteExt, BufReader},
    process::{ChildStdin, Command},
    sync::mpsc,
};

#[derive(Debug, Clone, PartialEq)]
struct AppState {
    logs: Vec<String>,
    engine: String,
    pct: u16,
    running: bool,
    repo_map_source: Option<String>,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            logs: vec!["r: run  m: repository map  q: quit".into()],
            engine: "idle".into(),
            pct: 0,
            running: false,
            repo_map_source: None,
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
            HarnessEvent::ContractResult { name, status } => {
                self.logs.push(format!("contract {name}: {status}"));
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
            HarnessEvent::RepoMap { mermaid } => {
                self.repo_map_source = Some(mermaid);
                self.logs.push("repository map received".into());
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
                self.logs.push(format!("harness finished: {status}"));
            }
        }
        const MAX_LOGS: usize = 2_000;
        if self.logs.len() > MAX_LOGS {
            self.logs.drain(..self.logs.len() - MAX_LOGS);
        }
    }
}

#[tokio::main]
async fn main() -> Result<()> {
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

    let mut mermaid = MermaidView::new()?;
    let result = run_terminal(&mut child_stdin, &mut rx, &repo_root, &mut mermaid).await;
    let _ = child_stdin.shutdown().await;
    let _ = child.kill().await;
    result
}

async fn run_terminal(
    child_stdin: &mut ChildStdin,
    rx: &mut mpsc::UnboundedReceiver<HarnessEvent>,
    repo_root: &std::path::Path,
    mermaid: &mut MermaidView,
) -> Result<()> {
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend)?;
    let result = run_loop(&mut terminal, child_stdin, rx, repo_root, mermaid).await;
    disable_raw_mode()?;
    execute!(
        terminal.backend_mut(),
        LeaveAlternateScreen,
        DisableMouseCapture
    )?;
    terminal.show_cursor()?;
    result
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
                    match key.code {
                        KeyCode::Char('q') => {
                            send_command(child_stdin, &HarnessCommand::Cancel).await?;
                            break;
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
                        KeyCode::Char('m') => {
                            if mermaid.is_visible() {
                                mermaid.toggle();
                            } else if let Some(source) = &state.repo_map_source {
                                if let Err(error) = mermaid.set_diagram(source) {
                                    state.logs.push(format!("[diagram error] {error}"));
                                } else {
                                    mermaid.show();
                                }
                            } else {
                                send_command(
                                    child_stdin,
                                    &HarnessCommand::RepoMap {
                                        root: repo_root.display().to_string(),
                                        focus: String::new(),
                                    },
                                ).await?;
                                state.logs.push("building repository map…".into());
                            }
                        }
                        KeyCode::Esc if mermaid.is_visible() => mermaid.toggle(),
                        _ => {}
                    }
                }
            }
            maybe_harness = rx.recv() => {
                if let Some(event) = maybe_harness {
                    let is_repo_map = matches!(event, HarnessEvent::RepoMap { .. });
                    state.apply(event);
                    if is_repo_map {
                        if let Some(source) = &state.repo_map_source {
                            match mermaid.set_diagram(source) {
                                Ok(()) => mermaid.show(),
                                Err(error) => state.logs.push(format!("[diagram error] {error}")),
                            }
                        }
                    }
                }
            }
            _ = tick.tick() => {
                terminal.draw(|frame| draw(frame, &state, mermaid))?;
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
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(3), Constraint::Min(0)])
        .split(frame.area());
    let gauge = Gauge::default()
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(state.engine.as_str()),
        )
        .gauge_style(Style::default().fg(Color::Cyan))
        .percent(state.pct.min(100));
    frame.render_widget(gauge, chunks[0]);

    let items: Vec<ListItem> = state
        .logs
        .iter()
        .rev()
        .take(200)
        .rev()
        .map(|line| ListItem::new(line.as_str()))
        .collect();
    frame.render_widget(
        List::new(items).block(Block::default().borders(Borders::ALL).title("log")),
        chunks[1],
    );

    if mermaid.is_visible() {
        let area = centered_rect(86, 86, frame.area());
        frame.render_widget(Clear, area);
        frame.render_widget(
            Block::default()
                .borders(Borders::ALL)
                .title("Repository diagram · Esc/m to close"),
            area,
        );
        let inner = Rect {
            x: area.x + 1,
            y: area.y + 1,
            width: area.width.saturating_sub(2),
            height: area.height.saturating_sub(2),
        };
        if let Some(error) = mermaid.error() {
            frame.render_widget(
                Paragraph::new(error).wrap(Wrap { trim: true }),
                inner,
            );
        } else {
            mermaid.render(frame, inner);
        }
    }
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
}
