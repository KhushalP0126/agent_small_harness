#[allow(dead_code)]
mod mermaid_view;
mod protocol;

use std::fs;
use std::io;
use std::panic;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::time::Duration;

use anyhow::{Context, Result};
use crossterm::{
    cursor::Show,
    event::{
        DisableMouseCapture, EnableMouseCapture, Event as CtEvent, EventStream, KeyCode,
        KeyModifiers, MouseEventKind,
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
    widgets::{Block, Borders, Clear, Padding, Paragraph, Wrap},
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
    ResearchReview { text: String, path: String },
    ActionApproval { request: String, reason: String },
    ToolDiffReview { path: String, diff: String },
    Executing,
}

#[derive(Debug, Clone, PartialEq)]
struct BackendContext {
    model: String,
    prompt_tokens: u32,
    completion_tokens: u32,
    total_tokens: u32,
    context_window: u32,
    estimated_cost_usd: f64,
}

impl AppMode {
    fn label(&self) -> &'static str {
        match self {
            Self::Chat => "chat",
            Self::Questionnaire => "questionnaire",
            Self::DraftingSpec => "drafting spec",
            Self::SpecReview { .. } => "spec review",
            Self::ResearchReview { .. } => "research review",
            Self::ActionApproval { .. } => "approval required",
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
    numbered_options_available: bool,
    clarification_questions: Vec<ClarificationQuestion>,
    clarification_index: usize,
    clarification_answers: Vec<QuestionnaireAnswer>,
    questionnaire_other_active: bool,
    inspector_scroll: usize,
    activity_tick: usize,
    deepseek_configured: bool,
    deepseek_source: String,
    memory_path: String,
    preference_count: u32,
    architect_mode: String,
    local_model: String,
    validated_source: Option<String>,
    validated_language: String,
    validated_artifact_path: String,
    validated_source_visible: bool,
    code_scroll_y: usize,
    code_scroll_x: usize,
    local_context: Option<BackendContext>,
    api_context: Option<BackendContext>,
    session_cost_usd: f64,
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
            repo_files: Vec::new(),
            repo_variables: Vec::new(),
            repo_selected: 0,
            context_content: "m map · d history".into(),
            history_visible: false,
            history_runs: Vec::new(),
            history_selected: 0,
            history_detail: None,
            prompt: String::new(),
            // The composer is always focused: users can start typing as soon as
            // the TUI opens, without first activating an input mode.
            prompt_active: true,
            assistant_busy: false,
            numbered_options_available: false,
            clarification_questions: Vec::new(),
            clarification_index: 0,
            clarification_answers: Vec::new(),
            questionnaire_other_active: false,
            inspector_scroll: 0,
            activity_tick: 0,
            deepseek_configured: false,
            deepseek_source: "checking".into(),
            memory_path: ".tui_memory.json".into(),
            preference_count: 0,
            architect_mode: "auto".into(),
            local_model: "qwen2.5-coder:1.5b".into(),
            validated_source: None,
            validated_language: String::new(),
            validated_artifact_path: String::new(),
            validated_source_visible: false,
            code_scroll_y: 0,
            code_scroll_x: 0,
            local_context: None,
            api_context: None,
            session_cost_usd: 0.0,
        }
    }
}

impl AppState {
    fn context_remaining_percent(&self) -> u8 {
        let Some(context) = &self.local_context else {
            return 100;
        };
        let remaining = context.context_window.saturating_sub(context.total_tokens);
        ((u64::from(remaining) * 100) / u64::from(context.context_window)) as u8
    }

    fn local_context_summary(&self) -> String {
        let Some(context) = &self.local_context else {
            return "ctx —".into();
        };
        format!(
            "ctx {}% · {}/{}",
            self.context_remaining_percent(),
            context.total_tokens,
            context.context_window
        )
    }

    fn persist_context(&self, repo_root: &std::path::Path) {
        let mut lines = vec![
            "# TUI Session Context".to_string(),
            "".to_string(),
            format!("Mode: {}", self.mode.label()),
            format!("Engine: {} · {}%", self.engine, self.pct),
            format!("Local context: {}", self.local_context_summary()),
            format!(
                "API context: {}",
                context_summary(self.api_context.as_ref())
            ),
            format!("Session API cost: ${:.4}", self.session_cost_usd),
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
                architect_mode,
                local_model,
            } => {
                self.deepseek_configured = deepseek_configured;
                self.deepseek_source = source;
                self.memory_path = memory_path;
                self.preference_count = preference_count;
                self.architect_mode = architect_mode;
                self.local_model = local_model;
            }
            HarnessEvent::ContextUsage {
                backend,
                model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                context_window,
                estimated_cost_usd,
            } => {
                let context = BackendContext {
                    model,
                    prompt_tokens,
                    completion_tokens,
                    total_tokens,
                    context_window: context_window.max(1),
                    estimated_cost_usd: estimated_cost_usd.max(0.0),
                };
                if backend == "api" {
                    self.session_cost_usd += context.estimated_cost_usd;
                    self.api_context = Some(context);
                } else {
                    self.local_context = Some(context);
                }
            }
            HarnessEvent::AssistantStatus { stage, busy } => {
                self.assistant_busy = busy;
                self.engine = if busy { stage.clone() } else { "idle".into() };
                self.logs.push(format!(
                    "[work] {} {}",
                    humanize_stage(&stage),
                    if busy { "started" } else { "complete" }
                ));
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
            HarnessEvent::ActionApproval { request, reason } => {
                self.logs.push(format!(
                    "[approval] {reason}\nrequest: {request}\ny continue to a reviewed proposal · n cancel"
                ));
                self.mode = AppMode::ActionApproval { request, reason };
                self.prompt_active = false;
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
                self.inspector_scroll = 0;
                self.mode = AppMode::Questionnaire;
                self.prompt_active = false;
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
                self.inspector_scroll = 0;
                self.prompt_active = false;
                self.logs
                    .push("spec draft ready; review it and press y to execute or n to revise".into());
            }
            HarnessEvent::ResearchDraft { text, path } => {
                self.assistant_busy = false;
                self.engine = "idle".into();
                self.inspector_scroll = 0;
                self.prompt_active = false;
                self.mode = AppMode::ResearchReview {
                    text,
                    path: path.clone(),
                };
                self.logs
                    .push(format!("research saved to {path}; review it in the side panel"));
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
                self.logs.push(format!("[history] {} run(s) loaded", runs.len()));
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
                self.logs.push(format!("[history] loaded detail for {run_id}"));
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
                "[tool] #{turn} {tool} · {}{}",
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
                "[assistant] {answer}{}",
                if exhausted {
                    format!(" (tool loop stopped after {call_count} call(s))")
                } else {
                    String::new()
                }
            )),
            HarnessEvent::CodeExcerpt {
                path,
                start_line,
                content,
                truncated,
            } => self.logs.push(format!(
                "[code] {path} · line {start_line}{}\n{content}",
                if truncated { " · excerpt" } else { "" }
            )),
            HarnessEvent::ToolDiff {
                path,
                diff,
                replacements,
            } => {
                self.logs.push(format!(
                    "[diff] {path} · {replacements} reviewed change(s)\n{diff}\ny apply · n discard"
                ));
                self.mode = AppMode::ToolDiffReview { path, diff };
                self.prompt_active = false;
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
                self.prompt_active = true;
            }
            HarnessEvent::CheckResult {
                path,
                passed,
                findings,
            } => {
                self.logs.push(format!(
                    "[check] {path}: {} · {} finding(s)",
                    if passed { "pass" } else { "fail" },
                    findings.len()
                ));
                for finding in findings.into_iter().take(12) {
                    self.logs.push(format!(
                        "  [{}] {} · {}",
                        finding.severity, finding.engine, finding.summary
                    ));
                }
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
        !self.validated_source_visible
    }

    fn side_inspector_active(&self) -> bool {
        matches!(
            self.mode,
            AppMode::Questionnaire | AppMode::SpecReview { .. } | AppMode::ResearchReview { .. }
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
    let harness_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .context("resolve harness source directory")?
        .to_path_buf();
    // The bridge runs with the selected repository as its CWD. Keep this
    // checkout on Python's module search path so `-m harness_kernel.tui_bridge`
    // remains importable when inspecting an unrelated project.
    let python_path = match std::env::var_os("PYTHONPATH") {
        Some(existing) => std::env::join_paths([harness_root.as_os_str(), existing.as_os_str()])
            .context("compose Python module search path")?,
        None => harness_root.into_os_string(),
    };
    let mut child = Command::new(python)
        .args(["-m", "harness_kernel.tui_bridge"])
        .current_dir(&repo_root)
        .env("HARNESS_REPOSITORY_ROOT", &repo_root)
        .env("PYTHONPATH", python_path)
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
    let mut state = AppState {
        working_directory: std::fs::canonicalize(repo_root)
            .unwrap_or_else(|_| repo_root.to_path_buf())
            .display()
            .to_string(),
        ..AppState::default()
    };
    state.persist_context(repo_root);
    let mut term_events = EventStream::new();
    let mut tick = tokio::time::interval(Duration::from_millis(33));
    'event_loop: loop {
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
                            MouseEventKind::ScrollUp if state.side_inspector_active() => {
                                state.inspector_scroll = state.inspector_scroll.saturating_sub(3);
                            }
                            MouseEventKind::ScrollDown if state.side_inspector_active() => {
                                state.inspector_scroll = state.inspector_scroll.saturating_add(3);
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
                            KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                                send_command(child_stdin, &HarnessCommand::Cancel).await?;
                                break 'event_loop;
                            }
                            KeyCode::Esc => {
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
                            KeyCode::Enter
                                if !state.prompt.trim().is_empty() && !state.assistant_busy =>
                            {
                                let text = std::mem::take(&mut state.prompt);
                                send_prompt_command(child_stdin, &mut state, text, repo_root).await?;
                                state.prompt_active = true;
                            }
                            KeyCode::Enter => {}
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
                        KeyCode::Up if state.side_inspector_active() => {
                            state.inspector_scroll = state.inspector_scroll.saturating_sub(1);
                        }
                        KeyCode::Down if state.side_inspector_active() => {
                            state.inspector_scroll = state.inspector_scroll.saturating_add(1);
                        }
                        KeyCode::PageUp if state.side_inspector_active() => {
                            state.inspector_scroll = state.inspector_scroll.saturating_sub(20);
                        }
                        KeyCode::PageDown if state.side_inspector_active() => {
                            state.inspector_scroll = state.inspector_scroll.saturating_add(20);
                        }
                        KeyCode::Home if state.side_inspector_active() => {
                            state.inspector_scroll = 0;
                        }
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
                            state.prompt_active = true;
                            state.clarification_questions.clear();
                            state.clarification_answers.clear();
                            state.logs.push(
                                "questionnaire cancelled; continue refining the idea in chat"
                                    .into(),
                            );
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
                            if matches!(state.mode, AppMode::ActionApproval { .. }) =>
                        {
                            send_command(
                                child_stdin,
                                &HarnessCommand::ApproveAction { approved: true },
                            )
                            .await?;
                            state.mode = AppMode::Chat;
                            state.prompt_active = true;
                            state.logs.push(
                                "[work] permission granted; preparing the requested work".into(),
                            );
                        }
                        KeyCode::Char('n') | KeyCode::Esc
                            if matches!(state.mode, AppMode::ActionApproval { .. }) =>
                        {
                            send_command(
                                child_stdin,
                                &HarnessCommand::ApproveAction { approved: false },
                            )
                            .await?;
                            state.mode = AppMode::Chat;
                            state.prompt_active = true;
                            state.logs.push("[approval] request cancelled before work started".into());
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
                        KeyCode::Char('n') | KeyCode::Esc
                            if matches!(state.mode, AppMode::SpecReview { .. }) =>
                        {
                            state.mode = AppMode::Chat;
                            state.prompt_active = true;
                            state
                                .logs
                                .push("spec execution declined; return to chat to revise".into());
                        }
                        KeyCode::Esc if matches!(state.mode, AppMode::ResearchReview { .. }) => {
                            state.mode = AppMode::Chat;
                            state.prompt_active = true;
                            state.logs.push("research panel closed".into());
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
                            state.logs.push("[history] closed".into());
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

async fn send_prompt_command(
    stdin: &mut ChildStdin,
    state: &mut AppState,
    text: String,
    repo_root: &Path,
) -> Result<()> {
    let trimmed = text.trim();
    let (command, argument) = trimmed
        .split_once(char::is_whitespace)
        .map_or((trimmed, ""), |(command, argument)| {
            (command, argument.trim())
        });
    match command {
        "/help" => state.logs.push(
            "commands: /map /open /check <path> /history /research /spec /model /remember <note> /mention <path> /tools <task>"
                .into(),
        ),
        "/map" => {
            state.repo_mode = "diagram".into();
            send_command(
                stdin,
                &HarnessCommand::RepoMap {
                    root: repo_root.display().to_string(),
                    focus: String::new(),
                    mode: "diagram".into(),
                },
            )
            .await?;
            state.logs.push("building browser map…".into());
        }
        "/open" => {
            if let Some(url) = state.repo_map_url.as_deref() {
                open_localhost(url);
            } else {
                state.logs.push("map is not ready yet; use /map first".into());
            }
        }
        "/history" => {
            send_command(
                stdin,
                &HarnessCommand::History {
                    run_id: None,
                    limit: None,
                },
            )
            .await?;
            state.logs.push("loading run history…".into());
        }
        "/research" => {
            send_command(stdin, &HarnessCommand::DraftResearch).await?;
        }
        "/spec" => {
            send_command(stdin, &HarnessCommand::DraftSpec).await?;
            state.mode = AppMode::DraftingSpec;
        }
        "/model" => state.logs.push(format!(
            "default: DeepSeek API · local chores: {}",
            state.local_model
        )),
        "/check" if argument.is_empty() => {
            state.logs.push("usage: /check <repository-file>".into());
        }
        "/check" => {
            send_command(
                stdin,
                &HarnessCommand::Check {
                    path: argument.to_owned(),
                },
            )
            .await?;
        }
        "/tools" if argument.is_empty() => {
            state.logs.push("usage: /tools <repository task>".into());
        }
        "/tools" => {
            send_command(
                stdin,
                &HarnessCommand::Chat {
                    text: argument.to_owned(),
                },
            )
            .await?;
        }
        "/remember" | "/mention" => {
            send_command(stdin, &HarnessCommand::Chat { text }).await?;
        }
        command if command.starts_with('/') => {
            state.logs.push(format!("unknown command: {command}; use /help"));
        }
        _ => send_command(stdin, &HarnessCommand::Chat { text }).await?,
    }
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
            Constraint::Min(0),
            Constraint::Length(1),
            Constraint::Length(3),
        ])
        .split(frame.area());
    let content_constraints = if state.side_inspector_active() {
        [Constraint::Percentage(64), Constraint::Percentage(36)]
    } else {
        [Constraint::Percentage(100), Constraint::Length(0)]
    };
    let content = Layout::default()
        .direction(Direction::Horizontal)
        .constraints(content_constraints)
        .split(rows[0]);
    let log_lines = stream_lines(state);
    let viewport_height = usize::from(content[0].height);
    let max_log_offset = log_lines.len().saturating_sub(viewport_height);
    let from_bottom = state.log_scroll.min(max_log_offset);
    let log_offset = max_log_offset
        .saturating_sub(from_bottom)
        .min(u16::MAX as usize) as u16;
    frame.render_widget(
        Paragraph::new(Text::from(log_lines))
            .style(Style::default().fg(theme_foreground()))
            .wrap(Wrap { trim: false })
            .scroll((log_offset, 0)),
        content[0],
    );

    draw_status_row(frame, state, rows[1]);
    draw_composer(frame, state, rows[2]);

    if let AppMode::SpecReview { spec_text } = &state.mode {
        draw_spec_review(frame, state, spec_text, content[1]);
    }

    if let AppMode::ResearchReview { text, path } = &state.mode {
        draw_research_review(frame, state, text, path, content[1]);
    }

    if state.mode == AppMode::Questionnaire {
        draw_questionnaire(frame, state, content[1]);
    }

    if state.validated_source_visible {
        draw_validated_source(frame, state);
    }
}

fn draw_status_row(frame: &mut ratatui::Frame, state: &AppState, area: Rect) {
    let busy = state.assistant_busy || state.running;
    let activity = if busy { "working" } else { "ready" };
    let status = if state.assistant_busy {
        state.engine.as_str()
    } else if state.running {
        "running"
    } else {
        activity
    };
    let line = Line::from(vec![
        Span::styled(
            " ● ",
            Style::default().fg(if busy { theme_cyan() } else { Color::Green }),
        ),
        Span::styled(
            status.to_owned(),
            Style::default()
                .fg(theme_foreground())
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled("  ·  ", Style::default().fg(theme_muted())),
        Span::styled(
            compact_path(&state.working_directory),
            Style::default().fg(theme_muted()),
        ),
        Span::styled("  ·  ", Style::default().fg(theme_muted())),
        Span::styled(
            state.local_context_summary(),
            Style::default().fg(theme_cyan()),
        ),
        Span::styled("  ·  ", Style::default().fg(theme_muted())),
        Span::styled(
            format!("local {}", state.local_model),
            Style::default().fg(theme_purple()),
        ),
        Span::styled("  ·  ", Style::default().fg(theme_muted())),
        Span::styled(
            if state.deepseek_configured {
                "DeepSeek default".to_owned()
            } else {
                "DeepSeek unavailable".to_owned()
            },
            Style::default().fg(if state.deepseek_configured {
                Color::Green
            } else {
                theme_muted()
            }),
        ),
    ]);
    frame.render_widget(
        Paragraph::new(line).style(Style::default().bg(theme_status())),
        area,
    );
}

fn draw_composer(frame: &mut ratatui::Frame, state: &AppState, area: Rect) {
    let active = state.prompt_active
        || matches!(
            state.mode,
            AppMode::ActionApproval { .. } | AppMode::ToolDiffReview { .. }
        );
    let block = Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(if active { theme_cyan() } else { theme_border() }))
        .style(Style::default().bg(theme_panel()))
        .padding(Padding::horizontal(1));
    frame.render_widget(block, area);
    let input_area = Rect {
        x: area.x.saturating_add(2),
        y: area.y.saturating_add(1),
        width: area.width.saturating_sub(4),
        height: 1,
    };
    frame.render_widget(
        Paragraph::new(composer_text(state))
            .style(Style::default().bg(theme_panel()))
            .wrap(Wrap { trim: false }),
        input_area,
    );
}

fn draw_questionnaire(frame: &mut ratatui::Frame, state: &AppState, area: Rect) {
    let total = state.clarification_questions.len();
    let current = state.clarification_index.saturating_add(1).min(total);
    frame.render_widget(
        pane_block(
            format!(" Clarify · {current}/{total} "),
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

fn composer_text(state: &AppState) -> Text<'static> {
    if state.mode == AppMode::Questionnaire {
        return Text::from(Line::styled(
            "Select an answer from the clarification panel.",
            Style::default().fg(theme_muted()),
        ));
    }
    if matches!(state.mode, AppMode::SpecReview { .. }) {
        return Text::from(Line::styled(
            "Review required before execution.",
            Style::default().fg(theme_muted()),
        ));
    }
    if matches!(state.mode, AppMode::ResearchReview { .. }) {
        return Text::from(Line::styled(
            "Research is saved and open in the side panel.",
            Style::default().fg(theme_muted()),
        ));
    }
    if matches!(state.mode, AppMode::ActionApproval { .. }) {
        return Text::from(Line::styled(
            "Permission required before repository changes.",
            Style::default().fg(Color::Yellow),
        ));
    }
    if matches!(state.mode, AppMode::ToolDiffReview { .. }) {
        return Text::from(Line::styled(
            "Reviewed change awaiting approval.",
            Style::default().fg(Color::LightCyan),
        ));
    }
    if state.prompt.is_empty() {
        let placeholder = "Ask anything…▍";
        return Text::from(Line::styled(
            placeholder,
            Style::default().fg(theme_muted()),
        ));
    }
    let mut lines = state
        .prompt
        .lines()
        .enumerate()
        .map(|(index, line)| {
            Line::from(vec![
                Span::styled(
                    if index == 0 { "> " } else { "  " },
                    Style::default()
                        .fg(theme_cyan())
                        .add_modifier(Modifier::BOLD),
                ),
                Span::styled(line.to_owned(), Style::default().fg(theme_foreground())),
            ])
        })
        .collect::<Vec<_>>();
    if state.prompt.ends_with('\n') {
        lines.push(Line::from(vec![
            Span::styled("  ", Style::default().fg(theme_cyan())),
            Span::styled("▍", Style::default().fg(theme_cyan())),
        ]));
    } else if state.prompt_active {
        if let Some(last) = lines.last_mut() {
            last.spans
                .push(Span::styled("▍", Style::default().fg(theme_cyan())));
        }
    }
    Text::from(lines)
}

fn stream_lines(state: &AppState) -> Vec<Line<'static>> {
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
        format!("{} {}%", state.engine, state.pct)
    } else {
        "ready".into()
    };
    let mut lines = vec![
        Line::from(vec![
            Span::styled(
                format!("codex {marker}"),
                Style::default()
                    .fg(theme_cyan())
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                format!(" · {status} · stream",),
                Style::default().fg(theme_muted()),
            ),
        ]),
        stream_divider(),
    ];
    if state.logs.is_empty() {
        lines.push(Line::styled(
            "• ready",
            Style::default().fg(if busy {
                Color::Cyan
            } else {
                theme_foreground()
            }),
        ));
        lines.push(Line::styled(
            "└ Enter to compose or send · /help lists commands",
            Style::default().fg(theme_muted()),
        ));
        return lines;
    }
    for (index, line) in state.logs.iter().enumerate() {
        if index > 0 && line.starts_with("[you] ") {
            lines.push(stream_divider());
        }
        lines.extend(stream_log_lines(line));
    }
    if state.history_visible {
        lines.push(stream_divider());
        lines.extend(stream_history_lines(state));
    }
    lines
}

fn stream_history_lines(state: &AppState) -> Vec<Line<'static>> {
    if let Some(detail) = &state.history_detail {
        let mut lines = vec![Line::from(vec![
            Span::styled("• ", Style::default().fg(Color::Magenta)),
            Span::styled(
                "history  ",
                Style::default()
                    .fg(Color::Magenta)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::styled(
                "run detail · d/Esc close",
                Style::default().fg(theme_muted()),
            ),
        ])];
        lines.extend(detail.lines().map(|line| {
            Line::from(vec![
                Span::styled("└ ", Style::default().fg(theme_muted())),
                Span::styled(line.to_owned(), Style::default().fg(theme_foreground())),
            ])
        }));
        return lines;
    }
    let mut lines = vec![Line::from(vec![
        Span::styled("• ", Style::default().fg(Color::Magenta)),
        Span::styled(
            "history  ",
            Style::default()
                .fg(Color::Magenta)
                .add_modifier(Modifier::BOLD),
        ),
        Span::styled(
            "Up/Down select · Enter detail · d/Esc close",
            Style::default().fg(theme_muted()),
        ),
    ])];
    if state.history_runs.is_empty() {
        lines.push(Line::styled(
            "└ no checkpointed runs found",
            Style::default().fg(theme_muted()),
        ));
        return lines;
    }
    lines.extend(state.history_runs.iter().enumerate().map(|(index, run)| {
        let selected = index == state.history_selected;
        Line::from(vec![
            Span::styled(
                if selected { "└ > " } else { "└   " },
                Style::default().fg(if selected {
                    Color::LightCyan
                } else {
                    theme_muted()
                }),
            ),
            Span::styled(
                history_run_summary(run),
                Style::default()
                    .fg(if selected {
                        Color::LightCyan
                    } else {
                        theme_foreground()
                    })
                    .add_modifier(if selected {
                        Modifier::BOLD
                    } else {
                        Modifier::empty()
                    }),
            ),
        ])
    }));
    lines
}

fn history_run_summary(run: &RunSummary) -> String {
    let target = if run.target.trim().is_empty() {
        "legacy artifact"
    } else {
        run.target.as_str()
    };
    let status = if run.final_status.trim().is_empty() {
        "status unavailable"
    } else {
        run.final_status.as_str()
    };
    let attempts = if run.target.trim().is_empty() && run.final_status.trim().is_empty() {
        "attempts —".into()
    } else {
        format!("{} attempts", run.attempt_count)
    };
    format!("{} · {target} · {status} · {attempts}", run.run_id)
}

fn stream_divider() -> Line<'static> {
    Line::styled("─".repeat(72), Style::default().fg(Color::DarkGray))
}

fn stream_log_lines(line: &str) -> Vec<Line<'static>> {
    let trimmed = line.trim_start();
    if trimmed.len() != line.len() {
        return vec![Line::from(vec![
            Span::styled("└ ", Style::default().fg(theme_muted())),
            Span::styled(trimmed.to_owned(), Style::default().fg(theme_foreground())),
        ])];
    }
    let role = [
        ("[you] ", "you", Color::Green, Color::White),
        ("[assistant] ", "assistant", Color::Cyan, Color::LightCyan),
        ("[code] ", "code", Color::LightBlue, Color::LightBlue),
        ("[diff] ", "diff", Color::LightCyan, Color::LightCyan),
        ("[history] ", "history", Color::Magenta, Color::LightMagenta),
        ("[tool] ", "tool", Color::Blue, Color::LightBlue),
        ("[work] ", "working", Color::Magenta, Color::LightMagenta),
        ("[approval] ", "permission", Color::Yellow, Color::Yellow),
        ("[memory] ", "memory", Color::Magenta, Color::White),
        ("[error] ", "error", Color::LightRed, Color::LightRed),
        ("[warning] ", "warning", Color::Yellow, Color::Yellow),
        (
            "[protocol warning] ",
            "warning",
            Color::Yellow,
            Color::Yellow,
        ),
    ]
    .into_iter()
    .find(|(prefix, _, _, _)| line.starts_with(prefix));

    let (label, label_color, content, content_color) =
        if let Some((prefix, label, label_color, content_color)) = role {
            (label, label_color, &line[prefix.len()..], content_color)
        } else {
            ("event", theme_muted(), line, theme_foreground())
        };
    let mut rendered = Vec::new();
    let mut in_code_block = false;
    for (index, content_line) in content.lines().enumerate() {
        let trimmed = content_line.trim_start();
        let is_fence = trimmed.starts_with("```");
        let line_color = if content_line.starts_with('+') && !content_line.starts_with("+++") {
            Color::LightGreen
        } else if content_line.starts_with('-') && !content_line.starts_with("---") {
            Color::LightRed
        } else if content_line.starts_with("@@") {
            Color::Yellow
        } else if in_code_block || is_fence {
            Color::LightBlue
        } else if label == "tool" && content_line.contains("failed") {
            Color::LightRed
        } else if label == "tool" && content_line.contains("completed") {
            Color::LightGreen
        } else {
            content_color
        };
        let marker = if index == 0 { "• " } else { "└ " };
        let mut spans = vec![Span::styled(marker, Style::default().fg(label_color))];
        if index == 0 {
            spans.push(Span::styled(
                format!("{label}  "),
                Style::default()
                    .fg(label_color)
                    .add_modifier(Modifier::BOLD),
            ));
        }
        let style =
            if label == "diff" && content_line.starts_with('+') && !content_line.starts_with("+++")
            {
                Style::default()
                    .fg(Color::LightGreen)
                    .bg(Color::Rgb(16, 57, 37))
            } else if label == "diff"
                && content_line.starts_with('-')
                && !content_line.starts_with("---")
            {
                Style::default()
                    .fg(Color::LightRed)
                    .bg(Color::Rgb(74, 29, 35))
            } else {
                Style::default().fg(line_color)
            };
        spans.push(Span::styled(content_line.to_owned(), style));
        rendered.push(Line::from(spans));
        if is_fence {
            in_code_block = !in_code_block;
        }
    }
    if rendered.is_empty() {
        rendered.push(Line::from(vec![
            Span::styled("• ", Style::default().fg(label_color)),
            Span::styled(
                format!("{label}  "),
                Style::default()
                    .fg(label_color)
                    .add_modifier(Modifier::BOLD),
            ),
        ]));
    }
    rendered
}

fn humanize_stage(stage: &str) -> String {
    stage.replace('_', " ")
}

fn compact_path(path: &str) -> String {
    let components = std::path::Path::new(path)
        .components()
        .filter_map(|component| component.as_os_str().to_str())
        .collect::<Vec<_>>();
    if components.len() <= 3 {
        return path.to_owned();
    }
    format!("…/{}", components[components.len() - 3..].join("/"))
}

fn context_summary(context: Option<&BackendContext>) -> String {
    let Some(context) = context else {
        return "ctx —".into();
    };
    format!(
        "ctx {}% · {}/{}",
        ((u64::from(context.context_window.saturating_sub(context.total_tokens)) * 100)
            / u64::from(context.context_window)) as u8,
        context.total_tokens,
        context.context_window
    )
}

fn theme_background() -> Color {
    Color::Rgb(3, 7, 18)
}

fn theme_panel() -> Color {
    Color::Rgb(8, 15, 29)
}

fn theme_status() -> Color {
    Color::Rgb(10, 20, 38)
}

fn theme_foreground() -> Color {
    Color::Rgb(229, 237, 248)
}

fn theme_muted() -> Color {
    Color::Rgb(126, 145, 169)
}

fn theme_border() -> Color {
    Color::Rgb(43, 67, 96)
}

fn theme_cyan() -> Color {
    Color::Rgb(0, 210, 255)
}

fn theme_purple() -> Color {
    Color::Rgb(182, 128, 255)
}

fn pane_block<'a>(title: impl Into<Line<'a>>, active: bool) -> Block<'a> {
    pane_block_accent(title, active, Color::Rgb(71, 85, 105))
}

fn pane_block_accent<'a>(title: impl Into<Line<'a>>, active: bool, accent: Color) -> Block<'a> {
    let color = if active { theme_cyan() } else { accent };
    Block::default()
        .borders(Borders::ALL)
        .border_style(Style::default().fg(color))
        .style(Style::default().bg(theme_panel()))
        .padding(Padding::horizontal(1))
        .title(title)
        .title_style(Style::default().fg(color).add_modifier(Modifier::BOLD))
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

fn draw_spec_review(frame: &mut ratatui::Frame, state: &AppState, spec_text: &str, area: Rect) {
    frame.render_widget(
        pane_block(
            " Spec draft · Up/Down scroll · y execute · n/Esc revise ",
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
    frame.render_widget(
        Paragraph::new(spec_text)
            .wrap(Wrap { trim: false })
            .scroll((state.inspector_scroll.min(u16::MAX as usize) as u16, 0)),
        inner,
    );
}

fn draw_research_review(
    frame: &mut ratatui::Frame,
    state: &AppState,
    text: &str,
    path: &str,
    area: Rect,
) {
    frame.render_widget(
        pane_block(
            format!(" Research · {path} · Up/Down scroll · Esc close "),
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
    frame.render_widget(
        Paragraph::new(text)
            .wrap(Wrap { trim: false })
            .scroll((state.inspector_scroll.min(u16::MAX as usize) as u16, 0)),
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
        assert!(state.side_inspector_active());
        assert!(state.main_output_active());
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
    fn research_draft_opens_in_the_side_inspector() {
        let mut state = AppState::default();
        state.apply(HarnessEvent::ResearchDraft {
            text: "# Research\n\n## Finding".into(),
            path: "docs/research/example.md".into(),
        });
        assert!(state.side_inspector_active());
        assert!(state.main_output_active());
        assert!(matches!(state.mode, AppMode::ResearchReview { .. }));
        assert!(state
            .logs
            .last()
            .unwrap()
            .contains("docs/research/example.md"));
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
    fn tool_diff_stays_inline_and_resolution_returns_to_chat() {
        let mut state = AppState::default();
        state.apply(HarnessEvent::ToolDiff {
            path: "rust_tui/src/main.rs".into(),
            diff: "--- a/rust_tui/src/main.rs\n+++ b/rust_tui/src/main.rs\n".into(),
            replacements: 1,
        });
        assert_eq!(
            state.mode,
            AppMode::ToolDiffReview {
                path: "rust_tui/src/main.rs".into(),
                diff: "--- a/rust_tui/src/main.rs\n+++ b/rust_tui/src/main.rs\n".into(),
            }
        );
        assert!(state.main_output_active());
        assert!(state.logs.last().unwrap().starts_with("[diff]"));

        state.apply(HarnessEvent::ToolDiffResolved {
            path: "rust_tui/src/main.rs".into(),
            applied: false,
            message: "diff discarded".into(),
        });
        assert_eq!(state.mode, AppMode::Chat);
    }

    #[test]
    fn sensitive_action_requires_an_explicit_approval_state() {
        let mut state = AppState::default();
        state.apply(HarnessEvent::ActionApproval {
            request: "Remove stale artifacts".into(),
            reason: "Deleting files or directories is destructive.".into(),
        });
        assert_eq!(
            state.mode,
            AppMode::ActionApproval {
                request: "Remove stale artifacts".into(),
                reason: "Deleting files or directories is destructive.".into(),
            }
        );
        assert!(state.main_output_active());
        assert!(state.logs.last().unwrap().starts_with("[approval]"));
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
        assert_eq!(state.context_remaining_percent(), 100);
        state.apply(HarnessEvent::ContextUsage {
            backend: "local".into(),
            model: "qwen2.5-coder:1.5b".into(),
            prompt_tokens: 3_200,
            completion_tokens: 896,
            total_tokens: 4_096,
            context_window: 8_192,
            estimated_cost_usd: 0.0,
        });
        assert_eq!(state.context_remaining_percent(), 50);
        state.apply(HarnessEvent::ContextUsage {
            backend: "api".into(),
            model: "deepseek-v4-pro".into(),
            prompt_tokens: 540,
            completion_tokens: 900,
            total_tokens: 1_440,
            context_window: 65_536,
            estimated_cost_usd: 0.0091,
        });
        assert_eq!(
            state.api_context.as_ref().map(|usage| usage.model.as_str()),
            Some("deepseek-v4-pro")
        );
        assert!((state.session_cost_usd - 0.0091).abs() < f64::EPSILON);
        state.apply(HarnessEvent::ConfigStatus {
            deepseek_configured: true,
            source: ".env:DEEPSEEK_API_KEY".into(),
            memory_path: ".tui_memory.json".into(),
            preference_count: 3,
            architect_mode: "auto".into(),
            local_model: "qwen2.5-coder:1.5b".into(),
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
        assert!(state.prompt_active);
    }

    #[test]
    fn composer_renders_a_single_line_prompt() {
        let state = AppState {
            prompt_active: true,
            prompt: "inspect the repository".into(),
            ..AppState::default()
        };
        let text = composer_text(&state);
        assert_eq!(text.lines.len(), 1);
        assert!(text.lines[0]
            .spans
            .iter()
            .any(|span| span.content == "inspect the repository"));
        assert!(text.lines[0].spans.iter().any(|span| span.content == "▍"));
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
        assert!(state.side_inspector_active());
        assert!(state.main_output_active());
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
    fn history_events_drive_inline_state() {
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
        assert!(stream_lines(&state)
            .iter()
            .any(|line| line.spans.iter().any(|span| span.content.contains("run-a"))));

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
    fn legacy_history_entries_have_readable_fallbacks() {
        let summary = history_run_summary(&RunSummary {
            run_id: "old-run".into(),
            target: String::new(),
            final_status: String::new(),
            attempt_count: 0,
        });
        assert_eq!(
            summary,
            "old-run · legacy artifact · status unavailable · attempts —"
        );
    }

    #[test]
    fn code_excerpt_is_rendered_in_the_stream() {
        let mut state = AppState::default();
        state.apply(HarnessEvent::CodeExcerpt {
            path: "src/main.py".into(),
            start_line: 1,
            content: "   1 │ def main():\n   2 │     return 0".into(),
            truncated: false,
        });
        assert!(stream_lines(&state).iter().any(|line| line
            .spans
            .iter()
            .any(|span| span.content.contains("src/main.py"))));
        assert!(stream_lines(&state).iter().any(|line| line
            .spans
            .iter()
            .any(|span| span.content.contains("return 0"))));
    }

    #[test]
    fn structured_repo_entries_are_retained_for_browser_mapping() {
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
        assert_eq!(state.repo_files[state.repo_selected].summary, "Second file");
        assert_eq!(state.repo_variables[state.repo_selected].imports, ["json"]);
        assert_eq!(
            state.repo_variables[state.repo_selected].variables,
            ["DATA"]
        );
    }
}
