"""One versioned declaration of non-secret settings shared by all execution entry points."""

SCHEMA_VERSION = 1
RUN_SETTINGS_KEY = "_run_settings"
SETTING_FIELDS = (
    "llm_model",
    "llm_base_url",
    "search_backends",
    "search_profile_ids",
    "max_sub_questions",
    "max_rounds",
    "max_concurrency",
    "results_per_search",
    "fulltext_enabled",
    "fulltext_max_chars",
    "require_corroboration",
    "max_tokens",
    "max_replans",
    "request_timeout",
    "llm_max_input_chars",
    "llm_max_output_tokens",
    "max_run_seconds",
    "orchestration_mode",
    "runtime_config_version",
)
