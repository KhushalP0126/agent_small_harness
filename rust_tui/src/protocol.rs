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
    DraftResearch,
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
    ApproveAction {
        approved: bool,
    },
    Check {
        path: String,
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
    ResearchReadiness,
    OpenSettings,
    SaveProviderSettings {
        provider: String,
        endpoint: String,
        model: String,
        #[serde(skip_serializing_if = "Option::is_none")]
        credential: Option<String>,
        cost_cap_usd: f64,
        local_development_confirmed: bool,
    },
    TestProviderConnection,
    ClearProviderCredential {
        provider: String,
    },
    SetContributionSplit {
        qwen: u8,
        api: u8,
        save_default: bool,
    },
    CostCapApproval {
        approved: bool,
    },
    SetPermissionMode {
        mode: String,
    },
    ClearContext,
    CompactContext {
        instructions: String,
    },
    ContextStatus,
    ListCheckpoints,
    Rewind {
        checkpoint_id: String,
        scope: String,
    },
    BranchCheckpoint {
        checkpoint_id: String,
    },
    ExtensionsStatus,
    McpStatus,
    RepairSessionAction {
        run_id: String,
        entrypoint: String,
        action: String,
    },
    Orchestrate {
        goal: String,
    },
    ApproveGraph {
        session_id: String,
        revision_hash: String,
    },
    InspectOrchestration {
        #[serde(default)]
        session_id: String,
    },
    OrchestrationAction {
        #[serde(default)]
        session_id: String,
        action: String,
        #[serde(default)]
        node_id: String,
        #[serde(default)]
        provider: String,
    },
    ReplayOrchestration {
        #[serde(default)]
        session_id: String,
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
pub struct GraphNodeEntry {
    pub id: String,
    pub kind: String,
    pub label: String,
    #[serde(default)]
    pub module: String,
    #[serde(default)]
    pub line: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GraphEdgeEntry {
    pub source: String,
    pub target: String,
    pub kind: String,
    #[serde(default)]
    pub label: String,
    #[serde(default)]
    pub line: u32,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct GateStatus {
    pub name: String,
    pub passed: bool,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ReadinessCategory {
    pub name: String,
    pub passed: bool,
    #[serde(default)]
    pub evidence: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CheckFinding {
    pub engine: String,
    pub severity: String,
    pub summary: String,
    #[serde(default)]
    pub details: String,
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
        #[serde(default)]
        deepseek_reachable: Option<bool>,
        source: String,
        memory_path: String,
        preference_count: u32,
        architect_mode: String,
        local_model: String,
    },
    ContextUsage {
        backend: String,
        model: String,
        prompt_tokens: u32,
        completion_tokens: u32,
        total_tokens: u32,
        context_window: u32,
        #[serde(default)]
        estimated_cost_usd: f64,
    },
    AssistantStatus {
        stage: String,
        busy: bool,
    },
    ChatMessage {
        role: String,
        content: String,
    },
    ActionApproval {
        request: String,
        reason: String,
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
    ResearchDraft {
        text: String,
        path: String,
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
        #[serde(default)]
        summary: String,
        #[serde(default)]
        nodes: Vec<GraphNodeEntry>,
        #[serde(default)]
        edges: Vec<GraphEdgeEntry>,
    },
    // Decoding-only compatibility for protocol-v4 bridges. Version 5 never
    // emits or opens this URL.
    RepoMapUrl {
        url: String,
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
    RepairSessionSnapshot {
        session_id: String,
        phase: String,
        goal: String,
        #[serde(default)]
        contract: String,
        #[serde(default)]
        worker: String,
        attempt: u32,
        max_attempts: u32,
        #[serde(default)]
        strategy: String,
        #[serde(default)]
        failure_kind: String,
        #[serde(default)]
        failure_location: String,
        #[serde(default)]
        diagnostic: String,
        #[serde(default)]
        counterexample: String,
        #[serde(default)]
        edit_ratio: f64,
        #[serde(default)]
        semantic_stagnant: bool,
        #[serde(default)]
        gates: Vec<GateStatus>,
        #[serde(default)]
        pending_action: String,
        #[serde(default)]
        termination_reason: String,
    },
    ResearchReadiness {
        score: u8,
        status: String,
        #[serde(default)]
        categories: Vec<ReadinessCategory>,
        #[serde(default)]
        blockers: Vec<String>,
    },
    SettingsState {
        provider: String,
        endpoint_hostname: String,
        endpoint: String,
        model: String,
        configured: bool,
        last_four: String,
        fingerprint_prefix: String,
        cost_cap_usd: f64,
        #[serde(default)]
        local_development_confirmed: bool,
    },
    ProviderConnectionResult {
        ok: bool,
        message: String,
    },
    ContributionState {
        qwen: u8,
        api: u8,
        remaining_api_budget: f64,
        #[serde(default)]
        telemetry: BTreeMap<String, serde_json::Value>,
    },
    CostCapApproval {
        approved: bool,
        remaining_api_budget: f64,
    },
    PermissionModeState {
        mode: String,
    },
    ContextState {
        summary: String,
        message_count: u32,
        #[serde(default)]
        checkpoint_ids: Vec<String>,
        #[serde(default)]
        permission_mode: String,
        #[serde(default)]
        contribution: BTreeMap<String, serde_json::Value>,
        #[serde(default)]
        remaining_api_budget: f64,
        #[serde(default)]
        cleared: bool,
    },
    CheckpointCreated {
        checkpoint_id: String,
        #[serde(default)]
        parent_id: String,
        #[serde(default)]
        changed_paths: Vec<String>,
    },
    CheckpointList {
        #[serde(default)]
        checkpoints: Vec<BTreeMap<String, serde_json::Value>>,
    },
    RewindResult {
        ok: bool,
        checkpoint_id: String,
        message: String,
    },
    SessionBranched {
        parent_session_id: String,
        session_id: String,
        checkpoint_id: String,
    },
    ExtensionsState {
        #[serde(default)]
        extensions: Vec<BTreeMap<String, serde_json::Value>>,
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
    CodeExcerpt {
        path: String,
        start_line: u32,
        content: String,
        #[serde(default)]
        truncated: bool,
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
    CheckResult {
        path: String,
        passed: bool,
        findings: Vec<CheckFinding>,
    },
    GraphProposal {
        session_id: String,
        goal: String,
        revision: u32,
        revision_hash: String,
        graph: serde_json::Value,
    },
    OrchestrationState {
        state: serde_json::Value,
    },
    OrchestrationReplay {
        session_id: String,
        external_actions: bool,
        events: Vec<serde_json::Value>,
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
                deepseek_reachable: Some(true),
                source: ".env:DEEPSEEK_API_KEY".into(),
                memory_path: ".tui_memory.json".into(),
                preference_count: 2,
                architect_mode: "auto".into(),
                local_model: "qwen2.5-coder:1.5b".into(),
            },
            HarnessEvent::ContextUsage {
                backend: "local".into(),
                model: "qwen2.5-coder:1.5b".into(),
                prompt_tokens: 10,
                completion_tokens: 5,
                total_tokens: 15,
                context_window: 8192,
                estimated_cost_usd: 0.0,
            },
            HarnessEvent::AssistantStatus {
                stage: "chat".into(),
                busy: true,
            },
            HarnessEvent::ChatMessage {
                role: "assistant".into(),
                content: "What should we build?".into(),
            },
            HarnessEvent::ActionApproval {
                request: "Remove src/legacy.py".into(),
                reason: "Deleting files or directories is destructive.".into(),
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
            HarnessEvent::ResearchDraft {
                text: "# Research".into(),
                path: "docs/research/example.md".into(),
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
                summary: "Repository architecture".into(),
                nodes: vec![GraphNodeEntry {
                    id: "module:main".into(),
                    kind: "module".into(),
                    label: "main".into(),
                    module: "main".into(),
                    line: 1,
                }],
                edges: vec![],
            },
            HarnessEvent::RepoMapView {
                mode: "variables".into(),
                content: "main.py\n  variables: state".into(),
            },
            HarnessEvent::RepoMapFiles {
                entries: vec![FileEntry {
                    path: "rust_tui/src/main.rs".into(),
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
            HarnessEvent::RepairSessionSnapshot {
                session_id: "run-123".into(),
                phase: "repair".into(),
                goal: "fix parser".into(),
                contract: "parse".into(),
                worker: "small_worker".into(),
                attempt: 1,
                max_attempts: 3,
                strategy: "json_patch".into(),
                failure_kind: "behavior_mismatch".into(),
                failure_location: "parse".into(),
                diagnostic: "wrong output".into(),
                counterexample: "empty input".into(),
                edit_ratio: 0.02,
                semantic_stagnant: true,
                gates: vec![GateStatus {
                    name: "behavior".into(),
                    passed: false,
                }],
                pending_action: "json_patch".into(),
                termination_reason: String::new(),
            },
            HarnessEvent::ResearchReadiness {
                score: 71,
                status: "blocked".into(),
                categories: vec![ReadinessCategory {
                    name: "qwen_local".into(),
                    passed: true,
                    evidence: vec!["raw.json".into()],
                }],
                blockers: vec!["missing live sessions".into()],
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
            HarnessEvent::CodeExcerpt {
                path: "rust_tui/src/main.rs".into(),
                start_line: 1,
                content: "   1 │ fn main() {}".into(),
                truncated: false,
            },
            HarnessEvent::ToolDiff {
                path: "rust_tui/src/main.rs".into(),
                diff: "--- a/rust_tui/src/main.rs\n+++ b/rust_tui/src/main.rs\n".into(),
                replacements: 1,
            },
            HarnessEvent::ToolDiffResolved {
                path: "rust_tui/src/main.rs".into(),
                applied: true,
                message: "diff applied".into(),
            },
            HarnessEvent::CheckResult {
                path: "src/main.py".into(),
                passed: false,
                findings: vec![CheckFinding {
                    engine: "engine-parse-contract".into(),
                    severity: "High".into(),
                    summary: "Draft parse failure".into(),
                    details: "invalid syntax".into(),
                }],
            },
            HarnessEvent::GraphProposal {
                session_id: "orch-1".into(),
                goal: "build parser".into(),
                revision: 1,
                revision_hash: "abc".into(),
                graph: serde_json::json!({"nodes": []}),
            },
            HarnessEvent::OrchestrationState {
                state: serde_json::json!({"status": "running"}),
            },
            HarnessEvent::OrchestrationReplay {
                session_id: "orch-1".into(),
                external_actions: false,
                events: vec![serde_json::json!({"sequence": 1})],
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
            HarnessCommand::DraftResearch,
            HarnessCommand::ExecuteSpec {
                text: "# Parser spec".into(),
            },
            HarnessCommand::ToolTask {
                text: "inspect the parser".into(),
                provider: "deepseek".into(),
            },
            HarnessCommand::ApplyToolDiff { approved: true },
            HarnessCommand::ApproveAction { approved: false },
            HarnessCommand::Check {
                path: "src/main.py".into(),
            },
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
            HarnessCommand::ResearchReadiness,
            HarnessCommand::OpenSettings,
            HarnessCommand::SaveProviderSettings {
                provider: "deepseek".into(),
                endpoint: "https://api.deepseek.com".into(),
                model: "deepseek-chat".into(),
                credential: Some("private-channel-only".into()),
                cost_cap_usd: 1.0,
                local_development_confirmed: false,
            },
            HarnessCommand::TestProviderConnection,
            HarnessCommand::ClearProviderCredential {
                provider: "deepseek".into(),
            },
            HarnessCommand::SetContributionSplit {
                qwen: 50,
                api: 50,
                save_default: false,
            },
            HarnessCommand::CostCapApproval { approved: false },
            HarnessCommand::SetPermissionMode {
                mode: "plan".into(),
            },
            HarnessCommand::ClearContext,
            HarnessCommand::CompactContext {
                instructions: "keep diffs".into(),
            },
            HarnessCommand::ContextStatus,
            HarnessCommand::ListCheckpoints,
            HarnessCommand::Rewind {
                checkpoint_id: "cp-123".into(),
                scope: "both".into(),
            },
            HarnessCommand::BranchCheckpoint {
                checkpoint_id: "cp-123".into(),
            },
            HarnessCommand::ExtensionsStatus,
            HarnessCommand::McpStatus,
            HarnessCommand::RepairSessionAction {
                run_id: "run-123".into(),
                entrypoint: "coding_capability".into(),
                action: "resume".into(),
            },
            HarnessCommand::Orchestrate { goal: "build parser".into() },
            HarnessCommand::ApproveGraph { session_id: "orch-1".into(), revision_hash: "abc".into() },
            HarnessCommand::InspectOrchestration { session_id: "orch-1".into() },
            HarnessCommand::OrchestrationAction { session_id: "orch-1".into(), action: "retry".into(), node_id: "a".into(), provider: "api".into() },
            HarnessCommand::ReplayOrchestration { session_id: "orch-1".into() },
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

    #[test]
    fn protocol_v4_repository_events_remain_decodable() {
        let event = parse_event_line(
            r#"{"type":"repo_map","mermaid":"legacy","summary":"Repository architecture"}"#,
        );
        assert!(matches!(
            event,
            HarnessEvent::RepoMap { nodes, edges, .. } if nodes.is_empty() && edges.is_empty()
        ));
        assert!(matches!(
            parse_event_line(r#"{"type":"repo_map_url","url":"http://127.0.0.1"}"#),
            HarnessEvent::RepoMapUrl { .. }
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
