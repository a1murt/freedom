/* Types mirror docs/API.md (v1) plus fields observed on the running backend. */

export type Channel = "agent chat" | "voice call" | string;
export type Language = "RU" | "KZ" | "EN" | string;
export type Speaker = "client" | "operator" | "bot" | "system";
export type Segment = "pre" | "operator" | "internal" | "post";
export type ReviewState = "none" | "under_review" | "reviewed" | "confirmed" | "changed";
export type Provider = "anthropic" | "local" | "ensemble" | string;

export interface LatestEvaluation {
  id: number;
  status: "queued" | "running" | "completed" | "failed" | string;
  total_score: number | null;
  final_total_score: number | null;
  critical_error: boolean;
  provider: Provider;
  mode: "single" | "ensemble" | "partial" | string;
  model?: string | null;
  cost_usd: number | null;
  checklist_version_id: number;
  completed_at: string | null;
  error?: string | null;
}

export interface ConversationSummary {
  id: string;
  channel: Channel;
  language: Language;
  operator_name: string | null;
  turn_count: number;
  has_audio: boolean;
  first_response_seconds: number | null;
  terminal_event?: string | null;
  imported_at?: string;
  dataset_id?: number | null;
  latest_evaluation: LatestEvaluation | null;
  review_state: ReviewState;
}

export interface Turn {
  idx: number;
  timestamp_raw: string | null;
  offset_seconds: number | null;
  speaker: Speaker;
  text: string;
  is_duplicate: boolean;
  segment: Segment;
  pii_masked_text?: string | null;
}

export interface ConversationDetail extends ConversationSummary {
  turns: Turn[];
  audio_url_available: boolean;
  meta?: {
    identity_confirmed_event?: boolean;
    escalation_idx?: number | null;
    post_start_idx?: number | null;
    duplicates?: number;
    speakers?: Record<string, number>;
    operator_start_idx?: number | null;
  };
}

export interface ConversationList {
  items: ConversationSummary[];
  total: number;
}

export interface ImportResult {
  dataset_id: number;
  imported: number;
  by_language: Record<string, number>;
  by_channel: Record<string, number>;
}

export interface AudioFeatures {
  duration_s: number | null;
  operator_talk_s?: number | null;
  client_talk_s?: number | null;
  talk_ratio_operator: number | null;
  operator_wpm: number | null;
  long_pauses: { start: number; end: number; duration?: number }[];
  long_pauses_count?: number | null;
  overlaps: number | null;
  loudness_range_lu: number | null;
  hold_segments?: { start: number; end: number }[];
  repeated_operator_lines?: number | null;
  speech_rate_label?: string | null;
  summary_ru?: string | null;
}

/* Checklists */

export interface CriterionOption {
  value: string | number;
  label: string;
  points: number;
  anchor?: string | null;
}

export interface JudgeSpec {
  definition?: string;
  decision_steps?: string[];
  na_conditions?: string[];
  evidence_requirement?: string;
  channel_notes?: Record<string, string>;
  pass_examples?: string[];
  fail_examples?: string[];
  [k: string]: unknown;
}

export interface Criterion {
  id?: number;
  code: string;
  section: string;
  title: string;
  order?: number;
  answer_type: "binary" | "scale";
  options: CriterionOption[];
  max_points: number;
  weight: number;
  na_allowed: boolean;
  na_condition: string | null;
  is_critical: boolean;
  critical_value?: string | number | null;
  instruction: string;
  channel_overrides: Record<string, { instruction?: string }>;
  judge_spec?: JudgeSpec | null;
}

export interface ChecklistSection {
  code: string;
  title: string;
}

export interface ScoringRules {
  total_scale?: number;
  na_policy?: string;
  rounding?: { mode: string; decimals: number };
  critical?: { mode: string };
  no_applicable_policy?: string;
  first_response_threshold_seconds?: number;
  excluded_from_score?: string[];
  [k: string]: unknown;
}

export interface ChecklistVersion {
  id: number;
  checklist_id: number;
  version_no: number;
  is_active: boolean;
  change_note: string | null;
  scoring_rules: ScoringRules;
  sections: ChecklistSection[];
  criteria: Criterion[];
  created_at?: string;
  created_from_version_id?: number | null;
}

export interface ChecklistVersionRef {
  id: number;
  version_no: number;
  created_at: string;
  change_note: string | null;
  is_active: boolean;
}

export interface Checklist {
  id: number;
  name: string;
  description?: string | null;
  created_at?: string;
  active_version: ChecklistVersion | null;
  versions: ChecklistVersionRef[];
}

export interface ChecklistBody {
  name?: string;
  description?: string;
  scoring_rules?: ScoringRules;
  sections?: ChecklistSection[];
  criteria: Omit<Criterion, "id" | "judge_spec">[];
  change_note?: string;
  compile_specs?: boolean;
}

export interface ChecklistDiff {
  from_version_id?: number;
  to_version_id?: number;
  added: string[];
  removed: string[];
  changed: { code: string; fields: string[]; requires_rejudge: boolean }[];
  scoring_rules_changed?: boolean;
  rejudge_codes?: string[];
}

/* Evaluations */

export interface Evidence {
  turn_idx: number;
  speaker: Speaker | string;
  quote: string;
  offset_seconds?: number | null;
  role: "supports" | "contradicts" | "context" | string;
}

export interface Vote {
  provider: string;
  model: string;
  answer: string | number | null;
  score: number | null;
  confidence: number | null;
}

export interface Claim {
  turn_idx: number;
  claim: string;
  status: string;
  note?: string | null;
}

export interface Decision {
  id: number;
  action: "confirm" | "change";
  new_answer: string | number | null;
  new_score: number | null;
  reason: string;
  reviewer_label: string;
  created_at: string;
}

export interface Appeal {
  id: number;
  evaluation_criterion_id: number;
  comment: string;
  author_label: string;
  status: "open" | "decided";
  created_at: string;
  decision: Decision | null;
}

export interface EvaluationCriterion {
  id: number;
  code: string;
  title: string;
  answer_type?: "binary" | "scale";
  options?: CriterionOption[];
  applicable: boolean;
  na_reason: string | null;
  answer: string | number | null;
  score: number | null;
  max_points: number;
  weight: number;
  weight_applied: number;
  earned: number;
  confidence: number | null;
  explanation: string;
  evidence: Evidence[];
  checked_span: { from_turn: number; to_turn: number } | null;
  flags: string[];
  claims?: Claim[];
  votes: Vote[] | null;
  review_state: ReviewState;
  final_answer: string | number | null;
  final_score: number | null;
  final_earned: number | null;
  appeal: Appeal | null;
  appeals?: Appeal[];
}

export interface EvaluationSection {
  code: string;
  title: string;
  score: number;
  max: number;
}

export interface Evaluation {
  id: number;
  conversation_id: string;
  checklist_version_id: number;
  status: string;
  provider: Provider;
  mode: string;
  model: string | null;
  prompt_version?: string;
  total_score: number | null;
  final_total_score: number | null;
  critical_error: boolean;
  sections: EvaluationSection[];
  cost_usd: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  latency_ms: number | null;
  job_id?: string | null;
  error?: string | null;
  created_at: string;
  completed_at: string | null;
  review_state?: ReviewState;
  criteria?: EvaluationCriterion[];
}

export interface TraceEntry {
  id?: number;
  criterion_code: string;
  kind?: string;
  provider: string;
  model: string;
  prompt_version: string;
  request: unknown;
  response: unknown;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
  created_at: string;
  votes?: Vote[] | null;
}

export interface RunBody {
  conversation_ids?: string[] | null;
  all?: boolean;
  checklist_version_id?: number | null;
  provider: Provider;
  model?: string | null;
  mode: "full" | "partial";
  base_version_id?: number | null;
  only_codes?: string[] | null;
  dry_run?: boolean;
}

export interface RunResult {
  job_id?: string;
  queued: number;
  estimated_cost_usd: number;
  rejudge_codes?: string[] | null;
  checklist_version_id?: number;
}

export interface JobLog {
  ts: string;
  level: string;
  message: string;
}

export interface Job {
  id: string;
  type: string;
  status: "queued" | "running" | "completed" | "failed" | "cancelled" | string;
  total: number;
  done: number;
  failed: number;
  cost_usd: number;
  params?: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
  created_at?: string | null;
  log: JobLog[];
}

/* Appeals */

export interface AppealWithContext extends Appeal {
  conversation_id: string;
  evaluation_id: number;
  code: string;
  title: string;
  answer_type: "binary" | "scale";
  options: CriterionOption[];
  na_allowed: boolean;
  applicable: boolean;
  answer: string | number | null;
  na_reason: string | null;
  score: number | null;
  max_points: number;
  weight: number;
  explanation: string;
  evidence: Evidence[];
  total_score: number | null;
  final_total_score: number | null;
  review_state: ReviewState;
  final_answer: string | number | null;
  language?: string;
  channel?: string;
}

export interface DecisionBody {
  action: "confirm" | "change";
  new_answer?: string | number | null;
  reason: string;
  reviewer_label?: string;
  add_to_fewshot?: boolean;
}

export interface PreviewResult {
  final_total_score: number | null;
  sections?: EvaluationSection[];
  section?: EvaluationSection;
  critical_error?: boolean;
}

/* Analytics */

export interface AnalyticsSummary {
  checklist_version_id: number | null;
  count: number;
  mean_total: number | null;
  median_total: number | null;
  critical_count: number;
  distribution: { bucket: string; n: number }[];
  by_language: { language: string; mean_total: number | null; n: number }[];
  by_channel: { channel: string; mean_total: number | null; n: number }[];
  criteria_heatmap: {
    code: string;
    title?: string;
    language: string;
    channel?: string;
    value?: number | null;
    yes_rate_or_mean: number | null;
    n?: number;
  }[];
  na_rates?: { code: string; na_rate: number }[];
  operators: { operator_name: string | null; n: number; mean_total: number | null; critical_count: number }[];
  worst: {
    conversation_id: string;
    evaluation_id?: number;
    channel: string;
    language: string;
    operator_name: string | null;
    total_score: number | null;
    final_total_score: number | null;
    critical_error: boolean;
  }[];
  agreement: { code: string; n_reviewed: number; agreement_rate: number | null; kappa: number | null }[];
  providers: {
    provider: string;
    model: string;
    n: number;
    mean_cost_usd: number | null;
    mean_latency_ms: number | null;
    total_cost_usd?: number | null;
  }[];
  first_response?: {
    mean_seconds: number | null;
    over_threshold_share: number | null;
    threshold_seconds: number | null;
    n: number;
  };
}

/* Settings */

export interface Settings {
  default_provider: Provider;
  default_model: string;
  secondary_model?: string | null;
  explanation_language: "ru" | "en" | "kk";
  pii_masking: boolean;
  fewshot_from_reviews: boolean;
  ensemble_members?: { provider: string; model: string }[];
  local_endpoint: string | null;
  local_model?: string | null;
  judge_effort?: string;
  max_parallel_criteria?: number;
  cost_rates?: Record<string, number[]>;
  fewshot_count?: number;
  anthropic_configured?: boolean;
}

export interface Health {
  ok: boolean;
  db: string;
  providers: Record<string, boolean>;
  organizer_api?: boolean;
}

export interface ApiErrorShape {
  error: { code: string; message: string };
}
