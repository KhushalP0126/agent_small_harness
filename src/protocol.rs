use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use tokio::io::{AsyncBufRead, AsyncBufReadExt};
use tokio::sync::mpsc;

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "cmd", rename_all = "snake_case")]
pub enum HarnessCommand {
    Run {
        entrypoint: String,
        #[serde(default)]
        args: Vec<String>,
    },
    Prompt {
        text: String,
    },
    Chat {
        text: String,
    },
    QuestionnaireComplete {
        answers: Vec<QuestionnaireAnswer>,
    },
    DraftSpec,
    ExecuteSpec {
        text: String,
    },
    ToolTask {
        text: String,
        provider: String,
    },
    ApplyToolDiff {
        approved: bool,
    },
    Cancel,
    RepoMap {
        root: String,
        #[serde(default)]
        focus: String,
        #[serde(default)]
        mode: String,
    },
    Compile {
        language: String,
        source: String,
    },
    ProfileSamples {
        loop_order: String,
        samples_ns: Vec<u64>,
        cache_misses: Option<u64>,
    },
    ComputeShield {
        phase: u8,
        tasks: Vec<ShieldTaskTokens>,
    },
    History {
        #[serde(default)]
        run_id: Option<String>,
        #[serde(default)]
        limit: Option<u32>,
    },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ShieldTaskTokens {
    pub task: String,
    pub baseline_tokens: u64,
    pub shielded_tokens: u64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RunSummary {
    pub run_id: String,
    #[serde(default)]
    pub target: String,
    #[serde(default)]
    pub final_status: String,
    #[serde(default)]
    pub attempt_count: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ContractSummary {
    pub name: String,
    #[serde(default)]
    pub signature: String,
    #[serde(default)]
    pub purpose: String,
    #[serde(default)]
    pub dependencies: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct FileEntry {
    pub path: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub symbols: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct VariableEntry {
    pub path: String,
    #[serde(default)]
    pub imports: Vec<String>,
    #[serde(default)]
    pub variables: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct QuestionOption {
    pub id: u8,
    pub text: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ClarificationQuestion {
    pub question_text: String,
    pub options: Vec<QuestionOption>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct QuestionnaireAnswer {
    pub question_text: String,
    pub answer: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum HarnessEvent {
    Ready {
        protocol_version: u16,
    },
    EngineProgress {
        engine: String,
        pct: u16,
    },
    Log {
        level: String,
        msg: String,
    },
    ConfigStatus {
        deepseek_configured: bool,
        source: String,
        memory_path: String,
        preference_count: u32,
    },
    AssistantStatus {
        stage: String,
        busy: bool,
    },
    ChatMessage {
        role: String,
        content: String,
    },
    Questionnaire {
        questions: Vec<ClarificationQuestion>,
    },
    ChatError {
        stage: String,
        message: String,
    },
    SpecDraft {
        text: String,
    },
    MemoryUpdated {
        preference: String,
        added: bool,
        count: u32,
    },
    ContractResult {
        name: String,
        status: String,
    },
    ContractQueuePlanned {
        contracts: Vec<ContractSummary>,
    },
    ContractProgress {
        name: String,
        status: String,
        attempt: u32,
        worker: String,
    },
    CompileGateResult {
        status: String,
        errors: Vec<String>,
    },
    ProfilingResult {
        loop_order: String,
        runtime_ns: u64,
        cache_misses: Option<u64>,
        #[serde(default)]
        spread_ns: u64,
    },
    ComputeShieldMetrics {
        phase: u8,
        tokens_baseline: u64,
        tokens_shielded: u64,
        delta: i64,
    },
    RepoMap {
        mermaid: String,
        #[serde(default)]
        summary: String,
    },
    RepoMapView {
        mode: String,
        content: String,
    },
    RepoMapFiles {
        entries: Vec<FileEntry>,
    },
    RepoMapVariables {
        entries: Vec<VariableEntry>,
    },
    HistoryList {
        runs: Vec<RunSummary>,
    },
    HistoryDetail {
        run_id: String,
        #[serde(default)]
        checkpoint: BTreeMap<String, serde_json::Value>,
    },
    Result {
        status: String,
        #[serde(default)]
        payload: BTreeMap<String, serde_json::Value>,
    },
    ValidatedSource {
        language: String,
        source: String,
        #[serde(default)]
        artifact_path: String,
    },
    ToolCall {
        turn: u32,
        tool: String,
        ok: bool,
        #[serde(default)]
        summary: String,
    },
    ToolAnswer {
        answer: String,
        exhausted: bool,
        call_count: u32,
    },
    ToolDiff {
        path: String,
        diff: String,
        replacements: u32,
    },
    ToolDiffResolved {
        path: String,
        applied: bool,
        message: String,
    },
    ProtocolError {
        line: String,
        error: String,
    },
    Done {
        #[serde(default)]
        status: String,
    },
}

pub fn parse_event_line(line: &str) -> HarnessEvent {
    serde_json::from_str(line).unwrap_or_else(|error| HarnessEvent::ProtocolError {
        line: line.to_owned(),
        error: error.to_string(),
    })
}

pub async fn read_harness_events<R>(reader: R, tx: mpsc::UnboundedSender<HarnessEvent>)
where
    R: AsyncBufRead + Unpin,
{
    let mut lines = reader.lines();
    while let Ok(Some(line)) = lines.next_line().await {
        if tx.send(parse_event_line(&line)).is_err() {
            break;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event_fixtures() -> Vec<HarnessEvent> {
        vec![
            HarnessEvent::Ready {
                protocol_version: 1,
            },
            HarnessEvent::EngineProgress {
                engine: "cost".into(),
                pct: 42,
            },
            HarnessEvent::Log {
                level: "info".into(),
                msg: "running".into(),
            },
            HarnessEvent::ConfigStatus {
                deepseek_configured: true,
                source: ".env:DEEPSEEK_API_KEY".into(),
                memory_path: ".tui_memory.json".into(),
                preference_count: 2,
            },
            HarnessEvent::AssistantStatus {
                stage: "chat".into(),
                busy: true,
            },
            HarnessEvent::ChatMessage {
                role: "assistant".into(),
                content: "What should we build?".into(),
            },
            HarnessEvent::Questionnaire {
                questions: vec![ClarificationQuestion {
                    question_text: "Choose a target".into(),
                    options: vec![
                        QuestionOption {
                            id: 1,
                            text: "CLI".into(),
                        },
                        QuestionOption {
                            id: 2,
                            text: "Other".into(),
                        },
                    ],
                }],
            },
            HarnessEvent::ChatError {
                stage: "chat".into(),
                message: "offline".into(),
            },
            HarnessEvent::SpecDraft {
                text: "# Spec".into(),
            },
            HarnessEvent::MemoryUpdated {
                preference: "keep responses concise".into(),
                added: true,
                count: 1,
            },
            HarnessEvent::ContractResult {
                name: "transform".into(),
                status: "accepted".into(),
            },
            HarnessEvent::ContractQueuePlanned {
                contracts: vec![ContractSummary {
                    name: "parse".into(),
                    signature: "def parse(text: str) -> dict".into(),
                    purpose: "Parse the input".into(),
                    dependencies: vec![],
                }],
            },
            HarnessEvent::ContractProgress {
                name: "parse".into(),
                status: "dispatched".into(),
                attempt: 0,
                worker: "small_worker".into(),
            },
            HarnessEvent::CompileGateResult {
                status: "pass".into(),
                errors: vec![],
            },
            HarnessEvent::ProfilingResult {
                loop_order: "MKN".into(),
                runtime_ns: 10,
                cache_misses: None,
                spread_ns: 2,
            },
            HarnessEvent::ComputeShieldMetrics {
                phase: 3,
                tokens_baseline: 100,
                tokens_shielded: 40,
                delta: 60,
            },
            HarnessEvent::RepoMap {
                mermaid: "flowchart LR\n  A --> B".into(),
                summary: "Repository architecture".into(),
            },
            HarnessEvent::RepoMapView {
                mode: "variables".into(),
                content: "main.py\n  variables: state".into(),
            },
            HarnessEvent::RepoMapFiles {
                entries: vec![FileEntry {
                    path: "src/main.rs".into(),
                    summary: "Rust TUI".into(),
                    symbols: vec!["main".into()],
                }],
            },
            HarnessEvent::RepoMapVariables {
                entries: vec![VariableEntry {
                    path: "main.py".into(),
                    imports: vec!["os".into()],
                    variables: vec!["state".into()],
                }],
            },
            HarnessEvent::HistoryList {
                runs: vec![RunSummary {
                    run_id: "run-123".into(),
                    target: "matrix.py".into(),
                    final_status: "accepted".into(),
                    attempt_count: 2,
                }],
            },
            HarnessEvent::HistoryDetail {
                run_id: "run-123".into(),
                checkpoint: BTreeMap::new(),
            },
            HarnessEvent::Result {
                status: "ok".into(),
                payload: BTreeMap::new(),
            },
            HarnessEvent::ValidatedSource {
                language: "python".into(),
                source: "def main():\n    return 0\n".into(),
                artifact_path: "artifacts/runs/run-123".into(),
            },
            HarnessEvent::ToolCall {
                turn: 1,
                tool: "read_file".into(),
                ok: true,
                summary: "completed".into(),
            },
            HarnessEvent::ToolAnswer {
                answer: "inspection complete".into(),
                exhausted: false,
                call_count: 1,
            },
            HarnessEvent::ToolDiff {
                path: "src/main.rs".into(),
                diff: "--- a/src/main.rs\n+++ b/src/main.rs\n".into(),
                replacements: 1,
            },
            HarnessEvent::ToolDiffResolved {
                path: "src/main.rs".into(),
                applied: true,
                message: "diff applied".into(),
            },
            HarnessEvent::ProtocolError {
                line: "{broken".into(),
                error: "expected value".into(),
            },
            HarnessEvent::Done {
                status: "completed".into(),
            },
        ]
    }

    #[test]
    fn every_command_variant_round_trips() {
        let commands = vec![
            HarnessCommand::Cancel,
            HarnessCommand::Run {
                entrypoint: "coding_capability".into(),
                args: vec!["--save-artifacts".into()],
            },
            HarnessCommand::Prompt {
                text: "Build a parser".into(),
            },
            HarnessCommand::Chat {
                text: "Help me plan a parser".into(),
            },
            HarnessCommand::QuestionnaireComplete {
                answers: vec![QuestionnaireAnswer {
                    question_text: "Choose a target".into(),
                    answer: "CLI".into(),
                }],
            },
            HarnessCommand::DraftSpec,
            HarnessCommand::ExecuteSpec {
                text: "# Parser spec".into(),
            },
            HarnessCommand::ToolTask {
                text: "inspect the parser".into(),
                provider: "qwen".into(),
            },
            HarnessCommand::ApplyToolDiff { approved: true },
            HarnessCommand::RepoMap {
                root: ".".into(),
                focus: String::new(),
                mode: "diagram".into(),
            },
            HarnessCommand::Compile {
                language: "c".into(),
                source: "int main(void) { return 0; }".into(),
            },
            HarnessCommand::ProfileSamples {
                loop_order: "MKN".into(),
                samples_ns: vec![10, 12, 11],
                cache_misses: None,
            },
            HarnessCommand::ComputeShield {
                phase: 3,
                tasks: vec![ShieldTaskTokens {
                    task: "matrix".into(),
                    baseline_tokens: 100,
                    shielded_tokens: 40,
                }],
            },
            HarnessCommand::History {
                run_id: Some("run-123".into()),
                limit: Some(5),
            },
            HarnessCommand::History {
                run_id: None,
                limit: None,
            },
        ];
        for command in commands {
            let encoded = serde_json::to_string(&command).unwrap();
            let decoded: HarnessCommand = serde_json::from_str(&encoded).unwrap();
            assert_eq!(decoded, command);
        }
    }

    #[test]
    fn every_event_variant_round_trips() {
        for event in event_fixtures() {
            let encoded = serde_json::to_string(&event).unwrap();
            let decoded: HarnessEvent = serde_json::from_str(&encoded).unwrap();
            assert_eq!(decoded, event);
        }
    }

    #[test]
    fn malformed_lines_become_protocol_errors() {
        assert!(matches!(
            parse_event_line("{broken"),
            HarnessEvent::ProtocolError { .. }
        ));
    }

    #[tokio::test]
    async fn reader_keeps_valid_events_after_a_malformed_line() {
        let input = b"{broken\n{\"type\":\"done\",\"status\":\"ok\"}\n";
        let (tx, mut rx) = mpsc::unbounded_channel();
        read_harness_events(&input[..], tx).await;
        assert!(matches!(
            rx.recv().await.unwrap(),
            HarnessEvent::ProtocolError { .. }
        ));
        assert_eq!(
            rx.recv().await.unwrap(),
            HarnessEvent::Done {
                status: "ok".into()
            }
        );
    }

    #[tokio::test]
    async fn reader_accepts_events_from_a_real_subprocess() {
        use std::process::Stdio;
        use tokio::io::BufReader;
        use tokio::process::Command;

        let mut child = Command::new("python3")
            .args([
                "-c",
                "print('{broken'); print('{\"type\":\"done\",\"status\":\"ok\"}')",
            ])
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        let stdout = child.stdout.take().unwrap();
        let (tx, mut rx) = mpsc::unbounded_channel();
        read_harness_events(BufReader::new(stdout), tx).await;
        assert!(matches!(
            rx.recv().await.unwrap(),
            HarnessEvent::ProtocolError { .. }
        ));
        assert_eq!(
            rx.recv().await.unwrap(),
            HarnessEvent::Done {
                status: "ok".into()
            }
        );
        assert!(child.wait().await.unwrap().success());
    }
}
