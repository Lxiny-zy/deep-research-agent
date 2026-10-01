// Generated from FastAPI OpenAPI. Run npm run api:generate; do not edit.
export interface paths {
    "/": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Index */
        get: operations["index__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agents": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Agents */
        get: operations["list_agents_api_agents_get"];
        put?: never;
        /** Create Agent */
        post: operations["create_agent_api_agents_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agents/prompt-preview": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Preview Role Prompt */
        post: operations["preview_role_prompt_api_agents_prompt_preview_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/agents/{agent_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Update Agent */
        put: operations["update_agent_api_agents__agent_id__put"];
        post?: never;
        /** Delete Agent */
        delete: operations["delete_agent_api_agents__agent_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/attachments": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Upload Attachment
         * @description 解析一个上传文件：返回附件（含全部片段，供创建任务时原样提交）与展示摘要。
         *
         *     解析在内存中进行，创建任务时片段随任务契约冻结进 checkpoint。PDF 另按内容哈希
         *     保存原文件，供论文精读工作区显示原版版面；其他格式的原文件不落盘。
         */
        post: operations["upload_attachment_api_attachments_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/behaviors": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Behaviors
         * @description 可选的角色行为模板（新建角色卡片时从中选一）。
         */
        get: operations["list_behaviors_api_behaviors_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/capabilities": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Capabilities */
        get: operations["get_capabilities_api_capabilities_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/config": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Config
         * @description 当前全局配置（密钥脱敏）。
         */
        get: operations["get_config_api_config_get"];
        /** Update Config */
        put: operations["update_config_api_config_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/config/quality-schema": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Quality Schema
         * @description 交付质量设置的字段说明：标签、分组、取值范围、默认值与悬浮帮助文本。
         */
        get: operations["get_quality_schema_api_config_quality_schema_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/datasets": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Parse Dataset Upload
         * @description 解析表格文件：返回每张可用工作表的 CSV、行数与列类型，以及被跳过的表和原因。
         *
         *     与附件一样不落盘：前端选定工作表后把该表的 CSV 随任务提交，服务端创建任务时
         *     按同一规则重新校验。多工作表由用户选择，这里不替用户挑。
         */
        post: operations["parse_dataset_upload_api_datasets_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/intent/assess": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Assess Intent
         * @description 澄清循环的一轮：判断信息够不够开始研究，不够就给出追问与候选项。
         *
         *     **这个端点不创建任何 run，也不写库。** 这正是它存在的理由——旧实现让澄清
         *     走完整的建 run 流程再 halt，于是历史列表里躺着一条状态 done、却什么都没
         *     研究的记录。把判定挪到建 run 之前，既不脏历史，也不需要引入「挂起态」
         *     （那会把崩溃恢复、租约 fencing、事件回放全部拖进多轮语义里）。
         *
         *     成本纪律：第一轮只跑规则 + 本地模型（零 token）。只有进入第二轮才让 LLM
         *     生成贴合具体提问的候选项——能走到第二轮说明情况确实复杂，值得花这次钱。
         */
        post: operations["assess_intent_api_intent_assess_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/models": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Models */
        get: operations["list_models_api_models_get"];
        put?: never;
        /** Create Model */
        post: operations["create_model_api_models_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/models/discover": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Discover Models */
        post: operations["discover_models_api_models_discover_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/models/test-config": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Test Model Config */
        post: operations["test_model_config_api_models_test_config_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/models/{profile_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Update Model */
        put: operations["update_model_api_models__profile_id__put"];
        post?: never;
        /** Delete Model */
        delete: operations["delete_model_api_models__profile_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/models/{profile_id}/test": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Test Model
         * @description 对模型档案发一个最小补全,验证 base_url/key/model 可用。
         */
        post: operations["test_model_api_models__profile_id__test_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/projects": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Projects */
        get: operations["list_projects_api_projects_get"];
        put?: never;
        /** Create Project */
        post: operations["create_project_api_projects_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/projects/{project_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Project */
        get: operations["get_project_api_projects__project_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/projects/{project_id}/corpora": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Corpora */
        get: operations["list_corpora_api_projects__project_id__corpora_get"];
        put?: never;
        /** Create Corpus */
        post: operations["create_corpus_api_projects__project_id__corpora_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/projects/{project_id}/sources": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Sources */
        get: operations["list_sources_api_projects__project_id__sources_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/projects/{project_id}/sources/import": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Import Source */
        post: operations["import_source_api_projects__project_id__sources_import_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/projects/{project_id}/sources/{source_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        post?: never;
        /** Delete Source */
        delete: operations["delete_source_api_projects__project_id__sources__source_id__delete"];
        options?: never;
        head?: never;
        /** Update Source */
        patch: operations["update_source_api_projects__project_id__sources__source_id__patch"];
        trace?: never;
    };
    "/api/projects/{project_id}/sources/{source_id}/chunks": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Source Chunks */
        get: operations["list_source_chunks_api_projects__project_id__sources__source_id__chunks_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/qa/conversations": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Conversations */
        get: operations["list_conversations_api_qa_conversations_get"];
        put?: never;
        /** Create Conversation */
        post: operations["create_conversation_api_qa_conversations_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/qa/conversations/{conversation_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Conversation */
        get: operations["get_conversation_api_qa_conversations__conversation_id__get"];
        put?: never;
        post?: never;
        /** Delete Conversation */
        delete: operations["delete_conversation_api_qa_conversations__conversation_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/qa/conversations/{conversation_id}/messages": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Ask */
        post: operations["ask_api_qa_conversations__conversation_id__messages_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/qa/conversations/{conversation_id}/messages/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Ask Stream
         * @description Run a normal persisted QA turn while keeping idle HTTP connections alive.
         */
        post: operations["ask_stream_api_qa_conversations__conversation_id__messages_stream_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/qa/conversations/{conversation_id}/requests/{request_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Request Status */
        get: operations["request_status_api_qa_conversations__conversation_id__requests__request_id__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/research": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Research
         * @deprecated
         * @description 兼容旧 SSE 客户端，但执行统一走持久化 run 交付链路。
         */
        get: operations["research_api_research_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/resource-preflight": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Resource Preflight */
        get: operations["resource_preflight_api_resource_preflight_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/roles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Roles
         * @description 可编排角色（供构建器角色选择器）：内置可组合角色 + 已启用自定义卡片。
         */
        get: operations["list_roles_api_roles_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Runs */
        get: operations["list_runs_api_runs_get"];
        put?: never;
        /** Create Run */
        post: operations["create_run_api_runs_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/batch_delete": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Batch Delete */
        post: operations["batch_delete_api_runs_batch_delete_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Run */
        get: operations["get_run_api_runs__run_id__get"];
        put?: never;
        post?: never;
        /** Delete Run */
        delete: operations["delete_run_api_runs__run_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/cancel": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Cancel Run */
        post: operations["cancel_run_api_runs__run_id__cancel_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/deliverables": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Deliverables
         * @description 交付登记：每个交付物的格式、角色、大小、哈希与验收结论，外加每道门的结果。
         */
        get: operations["get_deliverables_api_runs__run_id__deliverables_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/deliverables/{name}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Download Deliverable */
        get: operations["download_deliverable_api_runs__run_id__deliverables__name__get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/document": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Run Document
         * @description 结构化报告文档：证据装置随报告一起交付，不再只存在于前端的即时 join。
         *
         *     这是 Markdown / HTML / 打印三种导出的共同数据源。它与 ``GET /api/runs/{id}``
         *     并列而不是取代后者——``RunDetail`` 仍按原样返回 ``report.citations`` 等字段，
         *     既有前端与质量指标链路不受影响。
         */
        get: operations["get_run_document_api_runs__run_id__document_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/document.bib": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Run Document Bib
         * @description Download the same run's references as a deterministic BibTeX file.
         */
        get: operations["get_run_document_bib_api_runs__run_id__document_bib_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/document.bundle.zip": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Run Document Bundle
         * @description Download the complete non-secret reproducibility bundle for a run.
         */
        get: operations["get_run_document_bundle_api_runs__run_id__document_bundle_zip_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/document.csv": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Run Document Csv
         * @description Download one structured report table as UTF-8 CSV.
         *
         *     Reports can contain heterogeneous tables, so ``table_id`` is required
         *     when more than one ``TableBlock`` is present.  A run without a table (for
         *     example, while it is still streaming) returns an empty CSV body.
         */
        get: operations["get_run_document_csv_api_runs__run_id__document_csv_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/document.md": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Run Document Markdown
         * @description Download the report as Markdown, with the evidence apparatus included.
         *
         *     This is deliberately not the same bytes as ``report.markdown``.  That field
         *     is the synthesizer's prose alone; downloading it drops the very things that
         *     make the report auditable — the per-claim verification status, the quote
         *     that was matched, the snapshot hash.  ``render_markdown`` projects the same
         *     assembled document the CSV/XLSX/PDF exports use, so every format makes the
         *     same claims about the same run.
         */
        get: operations["get_run_document_markdown_api_runs__run_id__document_md_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/document.paper.pdf": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Run Document Paper Pdf
         * @description Compile a paper-style PDF from the fixed XeLaTeX template.
         */
        get: operations["get_run_document_paper_pdf_api_runs__run_id__document_paper_pdf_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/document.pdf": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Run Document Pdf
         * @description Download a server-rendered PDF when the optional PDF extra is installed.
         */
        get: operations["get_run_document_pdf_api_runs__run_id__document_pdf_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/document.tex": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Run Document Latex
         * @description Download the reproducible LaTeX source for an academic-style report.
         */
        get: operations["get_run_document_latex_api_runs__run_id__document_tex_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/document.xlsx": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Run Document Xlsx
         * @description Download one structured report table as an XLSX workbook.
         *
         *     ``openpyxl`` is an optional installation extra.  Delaying its import to
         *     ``render_xlsx`` keeps the API usable in minimal deployments; those
         *     deployments receive a clear 501 response only when this endpoint is
         *     requested.
         */
        get: operations["get_run_document_xlsx_api_runs__run_id__document_xlsx_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Events
         * @description 事件回放。after_seq 为「跳过前 N 条」的偏移语义（客户端传已收到的条数）。
         */
        get: operations["get_events_api_runs__run_id__events_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/narrative": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Narrative
         * @description 人话进度叙述：按阶段聚合的一句话进展，事件的纯函数（不调用模型）。
         */
        get: operations["get_narrative_api_runs__run_id__narrative_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/reader": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Reader
         * @description 论文精读工作区：这次任务的研究对象，以及每份能否显示原版 PDF。
         */
        get: operations["get_reader_api_runs__run_id__reader_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/reader/{document_id}/pdf": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Reader Pdf
         * @description 原版 PDF：只接受该任务登记过的文档；归属由鉴权依赖按 run_id 校验。
         */
        get: operations["get_reader_pdf_api_runs__run_id__reader__document_id__pdf_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/resume": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Resume Run */
        post: operations["resume_run_api_runs__run_id__resume_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/stream": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Stream Run */
        get: operations["stream_run_api_runs__run_id__stream_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/tags": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Set Tags */
        put: operations["set_tags_api_runs__run_id__tags_put"];
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/template": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Run Template
         * @description 本次运行使用的任务模板与任务契约（详情页展示「系统理解了什么」）。
         */
        get: operations["get_run_template_api_runs__run_id__template_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/workspace": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Workspace
         * @description 三栏详情页的结构数据：步骤、重规划记录与产物文件树。
         */
        get: operations["get_workspace_api_runs__run_id__workspace_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/runs/{run_id}/workspace/file": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Workspace File
         * @description 读取一个清单登记过的产物文件（按清单哈希复核后返回，不接受任意路径）。
         */
        get: operations["get_workspace_file_api_runs__run_id__workspace_file_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/search-keys": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Keys */
        get: operations["list_keys_api_search_keys_get"];
        put?: never;
        /** Create Key */
        post: operations["create_key_api_search_keys_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/search-keys/{key_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Update Key */
        put: operations["update_key_api_search_keys__key_id__put"];
        post?: never;
        /** Delete Key */
        delete: operations["delete_key_api_search_keys__key_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/search-keys/{key_id}/test": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Test Key
         * @description 对搜索 key 发一次最小检索,验证 key 可用。
         */
        post: operations["test_key_api_search_keys__key_id__test_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/search-profiles": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get Search Profiles */
        get: operations["get_search_profiles_api_search_profiles_get"];
        put?: never;
        /** Create Search Profile */
        post: operations["create_search_profile_api_search_profiles_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/search-profiles/{profile_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Update Search Profile */
        put: operations["update_search_profile_api_search_profiles__profile_id__put"];
        post?: never;
        /** Delete Search Profile */
        delete: operations["delete_search_profile_api_search_profiles__profile_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/search-profiles/{profile_id}/test": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /** Test Search Profile */
        post: operations["test_search_profile_api_search_profiles__profile_id__test_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/search-resources/impact": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Search Resource Impact
         * @description Explain current references before a resource is edited or disabled.
         */
        get: operations["search_resource_impact_api_search_resources_impact_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/tags": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Tags */
        get: operations["list_tags_api_tags_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/templates": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Templates
         * @description 全部科研任务模板（前端任务入口据此渲染）。
         */
        get: operations["list_templates_api_templates_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/templates/contract": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        put?: never;
        /**
         * Preview Contract
         * @description 预览系统将如何理解这次任务：用户提交前就能看到解析出的论文、章节与约束。
         */
        post: operations["preview_contract_api_templates_contract_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/tiers": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Tiers
         * @description 研究档位：轻量 / 标准 / 深度对应的运行上限。
         */
        get: operations["list_tiers_api_tiers_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/usage": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get Usage
         * @description 当前身份今日（UTC）的研究次数与 token 用量，以及部署配置的额度。
         */
        get: operations["get_usage_api_usage_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/workflows": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List Workflows
         * @description 可选的任务流程列表（供前端选择器）：内置预置 + 已启用的自定义工作流。
         */
        get: operations["list_workflows_api_workflows_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/workflows/custom": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List Custom Workflows */
        get: operations["list_custom_workflows_api_workflows_custom_get"];
        put?: never;
        /** Create Custom Workflow */
        post: operations["create_custom_workflow_api_workflows_custom_post"];
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/api/workflows/custom/{workflow_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        get?: never;
        /** Update Custom Workflow */
        put: operations["update_custom_workflow_api_workflows_custom__workflow_id__put"];
        post?: never;
        /** Delete Custom Workflow */
        delete: operations["delete_custom_workflow_api_workflows_custom__workflow_id__delete"];
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/healthz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Healthz
         * @description Backward-compatible liveness probe.
         */
        get: operations["healthz_healthz_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/livez": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Livez */
        get: operations["livez_livez_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/metrics": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Metrics Endpoint
         * @description Prometheus text exposition for internal service monitoring.
         */
        get: operations["metrics_endpoint_metrics_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/readyz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Readyz */
        get: operations["readyz_readyz_get"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /** AgentCardCreate */
        AgentCardCreate: {
            /** Behavior */
            behavior: string;
            /**
             * Description
             * @default
             */
            description?: string;
            /**
             * Display Name
             * @default
             */
            display_name?: string;
            /**
             * Enabled
             * @default true
             */
            enabled?: boolean;
            /**
             * Icon
             * @default bot
             */
            icon?: string;
            /** Model Profile Id */
            model_profile_id?: string | null;
            /** Name */
            name: string;
            /**
             * Prompt Mode
             * @default append
             * @enum {string}
             */
            prompt_mode?: "append" | "replace";
            /** Search Profile Ids */
            search_profile_ids?: string[] | null;
            /**
             * System Prompt
             * @default
             */
            system_prompt?: string;
        };
        /** AgentCardUpdate */
        AgentCardUpdate: {
            /** Behavior */
            behavior?: string | null;
            /** Description */
            description?: string | null;
            /** Display Name */
            display_name?: string | null;
            /** Enabled */
            enabled?: boolean | null;
            /** Icon */
            icon?: string | null;
            /** Model Profile Id */
            model_profile_id?: string | null;
            /** Prompt Mode */
            prompt_mode?: ("append" | "replace") | null;
            /** Search Profile Ids */
            search_profile_ids?: string[] | null;
            /** System Prompt */
            system_prompt?: string | null;
        };
        /** AgentCardView */
        AgentCardView: {
            /** Behavior */
            behavior: string;
            /**
             * Description
             * @default
             */
            description?: string;
            /**
             * Display Name
             * @default
             */
            display_name?: string;
            /**
             * Enabled
             * @default true
             */
            enabled?: boolean;
            /**
             * Icon
             * @default bot
             */
            icon?: string;
            /** Id */
            id: string;
            /** Model Profile Id */
            model_profile_id?: string | null;
            /** Model Profile Name */
            model_profile_name?: string | null;
            /** Name */
            name: string;
            /**
             * Prompt Mode
             * @default replace
             * @enum {string}
             */
            prompt_mode?: "append" | "replace";
            /** Search Profile Ids */
            search_profile_ids?: string[] | null;
            /**
             * System Prompt
             * @default
             */
            system_prompt?: string;
        };
        /** AskRequest */
        AskRequest: {
            /** Project Id */
            project_id?: string | null;
            /** Query */
            query: string;
            /** Request Id */
            request_id?: string | null;
            /** Sources */
            sources?: ("web" | "library")[];
        };
        /**
         * AssessRequest
         * @description 澄清循环的一轮输入。服务端不存任何东西——累积的答案由客户端携带，
         *     因此同样的请求体永远得到同样的判定（与 ``CreateRunRequest.history`` 同源的原则）。
         */
        AssessRequest: {
            answers?: components["schemas"]["IntentSlots"];
            /** History */
            history?: components["schemas"]["ConversationTurn"][];
            /** Query */
            query: string;
            /**
             * Round
             * @default 0
             */
            round?: number;
            /**
             * Skip
             * @default false
             */
            skip?: boolean;
        };
        /** AssessResponse */
        AssessResponse: {
            /**
             * Blocked
             * @default false
             */
            blocked?: boolean;
            /**
             * Gap
             * @default none
             */
            gap?: string;
            /**
             * Intent
             * @default
             */
            intent?: string;
            /** Options */
            options?: string[];
            /**
             * Question
             * @default
             */
            question?: string;
            /** Ready */
            ready: boolean;
            /**
             * Reason
             * @default
             */
            reason?: string;
            /**
             * Resolved Query
             * @default
             */
            resolved_query?: string;
        };
        /**
         * AttachmentUpload
         * @description 上传一个任务附件（Base64 编码的文件内容）。
         */
        AttachmentUpload: {
            /** Data Base64 */
            data_base64: string;
            /** Filename */
            filename: string;
            /**
             * Mime Type
             * @default
             */
            mime_type?: string;
        };
        /** BatchDeleteRequest */
        BatchDeleteRequest: {
            /** Ids */
            ids: string[];
        };
        /** BatchDeleteResponse */
        BatchDeleteResponse: {
            /** Deleted */
            deleted: number;
            /** Deleted Ids */
            deleted_ids?: string[];
            /** Skipped */
            skipped: number;
        };
        /** CancelRunResponse */
        CancelRunResponse: {
            /** Run Id */
            run_id: string;
            /** Status */
            status: string;
        };
        /**
         * ChartBlock
         * @description 一张图。**不含数据**——只指向源表与取哪几列（见模块 docstring 的不变量）。
         *
         *     ``form`` 的选择遵循"数据的任务决定形式"：
         *       * ``bar``——单一指标跨对象比**绝对量**。对象是无序名义类别（方法名），所以所有柱
         *         同一个颜色，**不能**按数值深浅上色（那会把柱长重复编码成色相）。柱长即数值，
         *         因此基线强制为零。
         *       * ``dot``——同样是单一指标跨对象，但要看的是**彼此差异**。点用位置编码，位置没有
         *         "从零开始"的语义，所以非零基线是诚实的。35 dB 基座上比 0.5 dB 差异用这个，
         *         而不是去截断柱状图的 Y 轴。
         *       * ``grouped_bar``——≤3 个指标并排，此时系列本身是主题，用分类色 + 图例。
         *       * ``scatter``——两个维度的权衡（如精度 vs 参数量）。
         *       * ``line``——沿连续轴的变化（光谱曲线、逐年趋势）。
         */
        ChartBlock: {
            /**
             * Caption
             * @default
             */
            caption?: string;
            /**
             * Emphasis
             * @description 要高亮的行标签；其余行转灰（emphasis 形式）
             * @default
             */
            emphasis?: string;
            /**
             * Form
             * @default bar
             * @enum {string}
             */
            form?: "bar" | "dot" | "grouped_bar" | "scatter" | "line";
            /** Id */
            id: string;
            /**
             * Kind
             * @default chart
             * @constant
             */
            kind?: "chart";
            /**
             * Source Table
             * @description TableBlock.id；图必有源表
             */
            source_table: string;
            /**
             * Title
             * @default
             */
            title?: string;
            /** Value Columns */
            value_columns?: string[];
            /**
             * X Column
             * @description scatter / line 的横轴列；为空则用行标签
             * @default
             */
            x_column?: string;
            /**
             * Y Label
             * @default
             */
            y_label?: string;
        };
        /**
         * ClarificationRequest
         * @description 需要向用户澄清时的结构化产物。
         *
         *     ``question`` 是要问的话，``options`` 是候选解读。给选项而不只给问题，
         *     是因为开放式追问（「你想问什么？」）把认知负担全推给用户，而候选解读
         *     让用户一次点选就能消歧。
         */
        ClarificationRequest: {
            /** Options */
            options?: string[];
            /** Question */
            question: string;
            /**
             * Reason
             * @default
             */
            reason?: string;
        };
        /**
         * ConfigUpdate
         * @description PUT /api/config 请求：全部可选，仅覆盖显式提供的字段。
         *
         *     密钥空/省略＝保持不变（避免脱敏表单回写清空）；llm_base_url 显式空串＝清空。
         */
        ConfigUpdate: {
            /** Fulltext Enabled */
            fulltext_enabled?: boolean | null;
            /** Fulltext Max Chars */
            fulltext_max_chars?: number | null;
            /** Llm Api Key */
            llm_api_key?: string | null;
            /** Llm Base Url */
            llm_base_url?: string | null;
            /** Llm Model */
            llm_model?: string | null;
            /** Max Concurrency */
            max_concurrency?: number | null;
            /** Max Rounds */
            max_rounds?: number | null;
            /** Max Run Seconds */
            max_run_seconds?: number | null;
            /** Max Sub Questions */
            max_sub_questions?: number | null;
            /** Quality */
            quality?: {
                [key: string]: unknown;
            } | null;
            /** Request Timeout */
            request_timeout?: number | null;
            /** Require Corroboration */
            require_corroboration?: boolean | null;
            /** Results Per Search */
            results_per_search?: number | null;
            /** Search Backends */
            search_backends?: string[] | null;
            /** Search Profile Ids */
            search_profile_ids?: string[] | null;
            /** Serper Api Key */
            serper_api_key?: string | null;
            /** Tavily Api Key */
            tavily_api_key?: string | null;
            /** Version */
            version?: number | null;
            /** Xai Api Key */
            xai_api_key?: string | null;
        };
        /**
         * ConfigView
         * @description GET /api/config 响应：密钥脱敏，只透露是否已设置 + 尾部 hint。
         */
        ConfigView: {
            /** Access */
            access?: {
                [key: string]: string;
            };
            /** Fulltext Enabled */
            fulltext_enabled: boolean;
            /** Fulltext Max Chars */
            fulltext_max_chars: number;
            /** Llm Api Key Hint */
            llm_api_key_hint: string;
            /** Llm Api Key Set */
            llm_api_key_set: boolean;
            /** Llm Base Url */
            llm_base_url: string | null;
            /** Llm Model */
            llm_model: string;
            /** Max Concurrency */
            max_concurrency: number;
            /** Max Rounds */
            max_rounds: number;
            /** Max Run Seconds */
            max_run_seconds: number;
            /** Max Sub Questions */
            max_sub_questions: number;
            /** Quality */
            quality?: {
                [key: string]: unknown;
            };
            /** Request Timeout */
            request_timeout: number;
            /** Require Corroboration */
            require_corroboration: boolean;
            /** Results Per Search */
            results_per_search: number;
            /** Search Backends */
            search_backends: string[];
            /**
             * Search Profile Ids
             * @default []
             */
            search_profile_ids?: string[];
            /** Serper Api Key Hint */
            serper_api_key_hint: string;
            /** Serper Api Key Set */
            serper_api_key_set: boolean;
            /** Tavily Api Key Hint */
            tavily_api_key_hint: string;
            /** Tavily Api Key Set */
            tavily_api_key_set: boolean;
            /**
             * Version
             * @default 0
             */
            version?: number;
            /** Xai Api Key Hint */
            xai_api_key_hint: string;
            /** Xai Api Key Set */
            xai_api_key_set: boolean;
        };
        /**
         * ContextResolution
         * @description 可回放的多轮上下文消解元数据。
         *
         *     旧调用方仍可只使用 ``context_resolved`` / ``resolved_query``；该结构是
         *     增量审计信息，不改变原有消解 API。
         */
        ContextResolution: {
            /**
             * Context Resolved
             * @default false
             */
            context_resolved?: boolean;
            dependency_signal?: components["schemas"]["IntentSignal"] | null;
            /** History Used */
            history_used?: components["schemas"]["ConversationTurn"][];
            /**
             * Raw Query
             * @default
             */
            raw_query?: string;
            /**
             * Reason
             * @default
             */
            reason?: string;
            /**
             * Resolved Query
             * @default
             */
            resolved_query?: string;
            /**
             * Resolver Tier
             * @default none
             * @enum {string}
             */
            resolver_tier?: "none" | "llm" | "fallback";
            /**
             * Resolver Version
             * @default context-v1
             */
            resolver_version?: string;
        };
        /** ContractPreviewRequest */
        ContractPreviewRequest: {
            /** Query */
            query: string;
            /** Strategy */
            strategy?: ("none" | "quick" | "deep") | null;
            /** Template */
            template: string;
        };
        /**
         * ConversationTurn
         * @description 一轮历史对话。只保留判定意图所必需的字段。
         *
         *     刻意不存完整报告：多轮消解需要的是「上一轮问了什么、被判成什么意图」，
         *     把整份报告塞进上下文既昂贵又会稀释信号。
         */
        ConversationTurn: {
            /**
             * Intent
             * @default unknown
             */
            intent?: string;
            /** Query */
            query: string;
            slots?: components["schemas"]["IntentSlots"];
        };
        /** Corpus */
        Corpus: {
            /** Created At */
            created_at?: string | null;
            /**
             * Description
             * @default
             */
            description?: string;
            /** Id */
            id: string;
            /** Name */
            name: string;
            /** Project Id */
            project_id: string;
            /**
             * Source Count
             * @default 0
             */
            source_count?: number;
            /** Updated At */
            updated_at?: string | null;
        };
        /** CreateConversation */
        CreateConversation: {
            /** Run Id */
            run_id?: string | null;
            /**
             * Title
             * @default
             */
            title?: string;
        };
        /** CreateCorpusRequest */
        CreateCorpusRequest: {
            /**
             * Description
             * @default
             */
            description?: string;
            /** Name */
            name: string;
        };
        /** CreateProjectRequest */
        CreateProjectRequest: {
            /**
             * Description
             * @default
             */
            description?: string;
            /** Name */
            name: string;
        };
        /** CreateRunRequest */
        CreateRunRequest: {
            /** Attachments */
            attachments?: {
                [key: string]: unknown;
            }[];
            /**
             * Clarified
             * @default false
             */
            clarified?: boolean;
            /** Dataset */
            dataset?: string | null;
            dataset_source?: components["schemas"]["DatasetSource"] | null;
            /**
             * Demo Data
             * @default false
             */
            demo_data?: boolean;
            /** Execution Plan */
            execution_plan?: {
                [key: string]: unknown;
            } | string | null;
            /** History */
            history?: components["schemas"]["ConversationTurn"][];
            params?: components["schemas"]["ResearchParams"] | null;
            /** Project Id */
            project_id?: string | null;
            /** Query */
            query: string;
            /** Strategy */
            strategy?: ("none" | "quick" | "deep") | null;
            /** Template */
            template?: string | null;
            /** Tier */
            tier?: ("light" | "standard" | "deep") | null;
            /** Workflow */
            workflow?: string | null;
        };
        /** CreateRunResponse */
        CreateRunResponse: {
            /** Run Id */
            run_id: string;
        };
        /** DatasetSource */
        DatasetSource: {
            /**
             * Filename
             * @default
             */
            filename?: string;
            /**
             * Sheet
             * @default
             */
            sheet?: string;
        };
        /**
         * DatasetUpload
         * @description 上传一个待分析的表格文件（CSV / TSV / XLSX，Base64 编码）。
         */
        DatasetUpload: {
            /** Data Base64 */
            data_base64: string;
            /** Filename */
            filename: string;
        };
        /** Event */
        Event: {
            /**
             * Attempt
             * @default 1
             */
            attempt?: number;
            /** Data */
            data?: {
                [key: string]: unknown;
            } | null;
            /**
             * Elapsed
             * @default 0
             */
            elapsed?: number;
            /**
             * Message
             * @default
             */
            message?: string;
            /** Seq */
            seq?: number | null;
            /** Stage */
            stage: string;
            /**
             * Tokens
             * @default 0
             */
            tokens?: number;
            /**
             * Tokens Estimated
             * @default false
             */
            tokens_estimated?: boolean;
            /**
             * Type
             * @enum {string}
             */
            type: "start" | "info" | "finding" | "round" | "token" | "report" | "done" | "error" | "cancelled";
            /**
             * Version
             * @default 1
             */
            version?: number;
        };
        /**
         * EvidenceRecord
         * @description 证据附录的一条记录：交互侧栏在纸上的等价物。
         *
         *     这里刻意把原先只活在 HTML ``title=`` 属性里的字段全部提升为正式内容——
         *     ``verification_reason``、``semantic_confidence``、``corroboration_reason`` 和完整
         *     ``content_hash``。tooltip 在触屏上不可达、打印时不输出，也就是说这些信息在
         *     HTML 移动端就已经在无声丢失，不是做 PDF 才出现的问题。
         */
        EvidenceRecord: {
            /** Citation */
            citation: number;
            /**
             * Claim Id
             * @default
             */
            claim_id?: string;
            /**
             * Conditions Label
             * @description 如 KAIST；10 scenes；28 波段
             * @default
             */
            conditions_label?: string;
            /**
             * Consistency Status
             * @default not_checked
             */
            consistency_status?: string;
            /**
             * Content Hash
             * @default
             */
            content_hash?: string;
            /**
             * Context
             * @default
             */
            context?: string;
            /**
             * Contradiction Reason
             * @default
             */
            contradiction_reason?: string;
            /** Contradicts Claim Ids */
            contradicts_claim_ids?: string[];
            /**
             * Corroboration Reason
             * @default
             */
            corroboration_reason?: string;
            /**
             * Corroboration Status
             * @default not_checked
             */
            corroboration_status?: string;
            /**
             * Independent Source Count
             * @default 0
             */
            independent_source_count?: number;
            /**
             * Quantity Label
             * @description 如 PSNR = 38.36 dB
             * @default
             */
            quantity_label?: string;
            /**
             * Quantity Reason
             * @default
             */
            quantity_reason?: string;
            /**
             * Quantity Status
             * @default not_applicable
             */
            quantity_status?: string;
            /**
             * Quote
             * @default
             */
            quote?: string;
            /**
             * Reference
             * @default
             */
            reference?: string;
            /**
             * Semantic Confidence
             * @default 0
             */
            semantic_confidence?: number;
            /**
             * Semantic Reason
             * @default
             */
            semantic_reason?: string;
            /**
             * Semantic Status
             * @default not_checked
             */
            semantic_status?: string;
            /**
             * Source Section
             * @default
             */
            source_section?: string;
            /**
             * Source Url
             * @default
             */
            source_url?: string;
            /**
             * Statement
             * @default
             */
            statement?: string;
            /**
             * Verbatim Verified
             * @default false
             */
            verbatim_verified?: boolean;
            /**
             * Verification Reason
             * @default
             */
            verification_reason?: string;
        };
        /**
         * EvidenceVerification
         * @description Program-produced evidence status for one finding.
         *
         *     The LLM may propose an evidence quote, but only the deterministic verifier may
         *     promote the status to ``verified``.
         */
        EvidenceVerification: {
            /**
             * Claim Id
             * @default
             */
            claim_id?: string;
            /**
             * Consistency Status
             * @default not_checked
             * @enum {string}
             */
            consistency_status?: "not_checked" | "clear" | "conflicted";
            /**
             * Contradiction Reason
             * @default
             */
            contradiction_reason?: string;
            /** Contradicts Claim Ids */
            contradicts_claim_ids?: string[];
            /** Corroborates Claim Ids */
            corroborates_claim_ids?: string[];
            /**
             * Corroboration Reason
             * @default
             */
            corroboration_reason?: string;
            /**
             * Corroboration Status
             * @default not_checked
             * @enum {string}
             */
            corroboration_status?: "not_checked" | "single_source" | "corroborated" | "disputed";
            /**
             * Evidence Context
             * @description 程序从检索快照中截取的证据上下文，不由模型生成
             * @default
             */
            evidence_context?: string;
            /**
             * Independent Source Count
             * @default 0
             */
            independent_source_count?: number;
            /**
             * Method
             * @default none
             * @enum {string}
             */
            method?: "none" | "normalized_quote";
            /**
             * Quantity Reason
             * @default
             */
            quantity_reason?: string;
            /**
             * Quantity Status
             * @default not_applicable
             * @enum {string}
             */
            quantity_status?: "not_applicable" | "verified" | "unsupported";
            /** Quote End */
            quote_end?: number | null;
            /** Quote Start */
            quote_start?: number | null;
            /**
             * Reason
             * @default
             */
            reason?: string;
            /**
             * Semantic Confidence
             * @default 0
             */
            semantic_confidence?: number;
            /**
             * Semantic Reason
             * @default
             */
            semantic_reason?: string;
            /**
             * Semantic Status
             * @default not_checked
             * @enum {string}
             */
            semantic_status?: "not_checked" | "supported" | "unsupported" | "uncertain";
            /**
             * Source Content Hash
             * @default
             */
            source_content_hash?: string;
            source_identity?: components["schemas"]["SourceIdentity"] | null;
            /**
             * Source Reference
             * @default
             */
            source_reference?: string;
            /**
             * Source Title
             * @default
             */
            source_title?: string;
            /**
             * Status
             * @default unverified
             * @enum {string}
             */
            status?: "unverified" | "verified";
        };
        /**
         * ExecutionPolicy
         * @description 由任务意图推导出的、可审计的执行建议。
         *
         *     这是建议元数据而非权限：显式 workflow、部署上限和安全门禁始终优先。
         *     字段全部有保守默认值，因此旧客户端只提交 ``intent`` 时仍可正常运行。
         */
        ExecutionPolicy: {
            /**
             * Answer Mode
             * @default report
             */
            answer_mode?: string;
            /**
             * Freshness
             * @default any
             * @enum {string}
             */
            freshness?: "any" | "recent" | "latest";
            /** Max Rounds */
            max_rounds?: number | null;
            /** Max Sub Questions */
            max_sub_questions?: number | null;
            /**
             * Parallelism
             * @default 1
             */
            parallelism?: number;
            /**
             * Rationale
             * @default
             */
            rationale?: string;
            /**
             * Requires Corroboration
             * @default false
             */
            requires_corroboration?: boolean;
            /**
             * Requires Reflection
             * @default true
             */
            requires_reflection?: boolean;
            /**
             * Source Strategy
             * @default broad
             */
            source_strategy?: string;
            /** Workflow */
            workflow?: string | null;
        };
        /**
         * ExperimentConditions
         * @description 数值成立的实验条件。
         *
         *     这不是可选的元数据，而是数值可比性的前提：同一个 PSNR 数字在 28 波段与 31
         *     波段、256×256 裁剪与全图、不同 mask 与训练集下并不可比。抄了数字不抄条件，
         *     对照表越整齐越误导。
         *
         *     字段按本项目面向的高光谱计算成像领域挑选，但都是通用形状（数据集 / 划分 /
         *     规模 / 协议 / 训练数据 / 硬件），换领域不需要改结构。
         */
        ExperimentConditions: {
            /**
             * Acquisition
             * @description 采集方式，如 simulated / real capture；原文未写则留空
             * @default
             */
            acquisition?: string;
            /**
             * Bands
             * @description 光谱波段数
             */
            bands?: number | null;
            /**
             * Calibration
             * @description 标定信息；原文未报告时留空
             * @default
             */
            calibration?: string;
            /**
             * Coding Mode
             * @description 编码方式，如 CASSI / coded mask
             * @default
             */
            coding_mode?: string;
            /**
             * Dataset
             * @description KAIST / CAVE / ICVL / 真实采集…
             * @default
             */
            dataset?: string;
            /**
             * Dispersive Element
             * @description 色散元件，如 prism / grating
             * @default
             */
            dispersive_element?: string;
            /**
             * Hardware
             * @default
             */
            hardware?: string;
            /**
             * Notes
             * @default
             */
            notes?: string;
            /**
             * Protocol
             * @description 其他关键口径：mask 类型、位移步长…
             * @default
             */
            protocol?: string;
            /**
             * Prototype Validation
             * @description 原型/真实系统验证信息；原文未报告时留空
             * @default
             */
            prototype_validation?: string;
            /**
             * Scenes
             * @description 场景数量或编号，如 10 scenes / S1–S10
             * @default
             */
            scenes?: string;
            /**
             * Spatial Size
             * @description 空间尺寸，如 256×256
             * @default
             */
            spatial_size?: string;
            /**
             * Spectral Range
             * @description 光谱范围，如 400–700 nm；原文未写则留空
             * @default
             */
            spectral_range?: string;
            /**
             * Split
             * @description 测试划分，如 10 scenes / test set
             * @default
             */
            split?: string;
            /**
             * Train Data
             * @default
             */
            train_data?: string;
        };
        /** FinalReportValidation */
        FinalReportValidation: {
            /**
             * Fallback
             * @default false
             */
            fallback?: boolean;
            /** Issues */
            issues?: string[];
            /**
             * Scope
             * @default citation_and_numbers
             * @constant
             */
            scope?: "citation_and_numbers";
            /**
             * Semantic Verification
             * @default false
             */
            semantic_verification?: boolean;
        };
        /** Finding */
        Finding: {
            conditions?: components["schemas"]["ExperimentConditions"] | null;
            /**
             * Confidence
             * @description 置信度 0~1
             * @default 0.7
             */
            confidence?: number;
            /**
             * Entity
             * @description 论断描述的对象，如 MST-L / SD-CASSI
             * @default
             */
            entity?: string;
            /**
             * Evidence Quote
             * @description 支持该发现的来源原文短句；必须逐字来自 source_url 对应内容
             * @default
             */
            evidence_quote?: string;
            quantity?: components["schemas"]["Quantity"] | null;
            /**
             * Source Url
             * @description 该发现的出处 URL（必须来自给定来源）
             */
            source_url: string;
            /**
             * Statement
             * @description 一条具体、自洽的事实/发现
             */
            statement: string;
            verification?: components["schemas"]["EvidenceVerification"];
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /** ImportSourceRequest */
        ImportSourceRequest: {
            /** Corpus Id */
            corpus_id: string;
            /**
             * Data Base64
             * @default
             */
            data_base64?: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "text" | "markdown" | "url" | "doi" | "pdf";
            /**
             * Mime Type
             * @default
             */
            mime_type?: string;
            /**
             * Origin Url
             * @default
             */
            origin_url?: string;
            /**
             * Text
             * @default
             */
            text?: string;
            /**
             * Title
             * @default
             */
            title?: string;
        };
        /**
         * IntentDecision
         * @description 一次意图判定的完整结果。
         *
         *     同时携带任务意图与风险意图：二者正交，互不覆盖（见模块 docstring）。
         *     ``tier`` 记录最终由哪一级决定，``escalated`` 记录是否真的调用了 LLM——
         *     这两个字段直接支撑「X% 请求 0 token 解决」这类成本结论。
         */
        IntentDecision: {
            clarification?: components["schemas"]["ClarificationRequest"] | null;
            /**
             * Confidence
             * @default 0
             */
            confidence?: number;
            context_resolution?: components["schemas"]["ContextResolution"] | null;
            /**
             * Context Resolved
             * @default false
             */
            context_resolved?: boolean;
            /**
             * Escalated
             * @default false
             */
            escalated?: boolean;
            execution_policy?: components["schemas"]["ExecutionPolicy"];
            /**
             * Intent
             * @default unknown
             */
            intent?: string;
            /**
             * Intent Version
             * @default intent-v2
             */
            intent_version?: string;
            /**
             * Reason
             * @default
             */
            reason?: string;
            /**
             * Resolved Query
             * @default
             */
            resolved_query?: string;
            /**
             * Risk
             * @default none
             * @enum {string}
             */
            risk?: "none" | "prompt_injection" | "system_prompt_probe" | "off_task_instruction" | "unsafe_content";
            /**
             * Risk Confidence
             * @default 0
             */
            risk_confidence?: number;
            /** Scores */
            scores?: {
                [key: string]: number;
            };
            /** Signals */
            signals?: components["schemas"]["IntentSignal"][];
            slots?: components["schemas"]["IntentSlots"];
            /**
             * Tier
             * @default fallback
             * @enum {string}
             */
            tier?: "rule" | "model" | "llm" | "fallback";
        };
        /**
         * IntentSignal
         * @description 一条命中的证据，便于把判定讲清楚（而不是只给一个标签）。
         */
        IntentSignal: {
            /** Code */
            code: string;
            /**
             * Detail
             * @default
             */
            detail?: string;
            /**
             * Tier
             * @enum {string}
             */
            tier: "rule" | "model" | "llm" | "fallback";
        };
        /**
         * IntentSlots
         * @description 从 query 中抽出的结构化约束。
         *
         *     全部可选：抽不到就是抽不到，绝不编造——槽位为空时下游退回原始 query 的
         *     自由文本表达，这比一个幻觉出来的「2024 年」安全得多。
         */
        IntentSlots: {
            /** Aspects */
            aspects?: string[];
            /**
             * Audience
             * @default
             */
            audience?: string;
            /**
             * Domain
             * @default
             */
            domain?: string;
            /** Entities */
            entities?: string[];
            /**
             * Evidence Level
             * @default
             */
            evidence_level?: string;
            /**
             * Freshness
             * @default
             */
            freshness?: string;
            /**
             * Geography
             * @default
             */
            geography?: string;
            /**
             * Language
             * @default
             */
            language?: string;
            /**
             * Output Format
             * @default
             */
            output_format?: string;
            /** Source Types */
            source_types?: string[];
            /**
             * Time Range
             * @default
             */
            time_range?: string;
        };
        /** KeyCreate */
        KeyCreate: {
            /** Api Key */
            api_key: string;
            /**
             * Enabled
             * @default true
             */
            enabled?: boolean;
            /**
             * Label
             * @default
             */
            label?: string;
            /**
             * Priority
             * @default 0
             */
            priority?: number;
            /**
             * Provider
             * @default tavily
             */
            provider?: string;
        };
        /** KeyUpdate */
        KeyUpdate: {
            /** Api Key */
            api_key?: string | null;
            /** Enabled */
            enabled?: boolean | null;
            /** Label */
            label?: string | null;
            /** Priority */
            priority?: number | null;
            /** Provider */
            provider?: string | null;
        };
        /** LibrarySource */
        LibrarySource: {
            /** Char Count */
            char_count: number;
            /**
             * Chunk Count
             * @default 0
             */
            chunk_count?: number;
            /** Content Hash */
            content_hash: string;
            /** Corpus Id */
            corpus_id: string;
            /** Created At */
            created_at?: string | null;
            /** Id */
            id: string;
            /**
             * Kind
             * @enum {string}
             */
            kind: "text" | "markdown" | "url" | "doi" | "pdf";
            /** Metadata */
            metadata?: {
                [key: string]: unknown;
            };
            /**
             * Mime Type
             * @default text/plain
             */
            mime_type?: string;
            /**
             * Origin Url
             * @default
             */
            origin_url?: string;
            /** Project Id */
            project_id: string;
            /**
             * Status
             * @default included
             * @enum {string}
             */
            status?: "included" | "excluded";
            /** Title */
            title: string;
            /** Updated At */
            updated_at?: string | null;
        };
        /** ModelDiscoveryResult */
        ModelDiscoveryResult: {
            /** Latency Ms */
            latency_ms: number;
            /** Models */
            models: string[];
        };
        /**
         * ModelProfileView
         * @description 对外视图：api_key 脱敏，只露是否已设置 + 尾部 hint。
         */
        ModelProfileView: {
            /** Api Key Hint */
            api_key_hint: string;
            /** Api Key Set */
            api_key_set: boolean;
            /** Base Url */
            base_url?: string | null;
            /** Context Window Tokens */
            context_window_tokens?: number | null;
            /** Id */
            id: string;
            /** Is Default */
            is_default: boolean;
            /** Max Output Tokens */
            max_output_tokens?: number | null;
            /** Model */
            model: string;
            /** Name */
            name: string;
            /**
             * Parameter Mode
             * @default temperature
             */
            parameter_mode?: string;
            /**
             * Reasoning Effort
             * @default medium
             */
            reasoning_effort?: string;
            /** Temperature */
            temperature: number;
        };
        /**
         * Overview
         * @description 报告头部的证据链概览。``blocked_sources`` 为 None＝本次事件流没有审计事件。
         */
        Overview: {
            /** Blocked Sources */
            blocked_sources?: number | null;
            /**
             * Conflicted
             * @default 0
             */
            conflicted?: number;
            /**
             * Corroborated
             * @default 0
             */
            corroborated?: number;
            /**
             * Records
             * @default 0
             */
            records?: number;
            /**
             * Semantically Supported
             * @default 0
             */
            semantically_supported?: number;
            /**
             * Verbatim Matched
             * @default 0
             */
            verbatim_matched?: number;
        };
        /**
         * PaperSection
         * @description A bounded paper section containing the same typed blocks as a report.
         *
         *     ``ReportDocument.blocks`` remains the compatibility projection used by old
         *     clients.  New assemblies additionally populate ``sections`` so publication
         *     exports do not have to infer chapter boundaries independently.
         */
        PaperSection: {
            /** Blocks */
            blocks?: (components["schemas"]["ProseBlock"] | components["schemas"]["TableBlock"] | components["schemas"]["ChartBlock"])[];
            /** Id */
            id: string;
            /**
             * Kind
             * @default other
             * @enum {string}
             */
            kind?: "abstract" | "introduction" | "related_work" | "methods" | "experiments" | "results" | "limitations" | "conclusion" | "data_availability" | "ethics" | "other";
            /**
             * Level
             * @default 1
             */
            level?: number;
            /** Title */
            title: string;
        };
        /** ProfileCreate */
        ProfileCreate: {
            /**
             * Api Key
             * @default
             */
            api_key?: string;
            /** Base Url */
            base_url?: string | null;
            /** Context Window Tokens */
            context_window_tokens?: number | null;
            /**
             * Is Default
             * @default false
             */
            is_default?: boolean;
            /** Max Output Tokens */
            max_output_tokens?: number | null;
            /**
             * Model
             * @default gpt-4o-mini
             */
            model?: string;
            /** Name */
            name: string;
            /**
             * Parameter Mode
             * @default temperature
             */
            parameter_mode?: string;
            /**
             * Reasoning Effort
             * @default medium
             */
            reasoning_effort?: string;
            /**
             * Temperature
             * @default 0.3
             */
            temperature?: number;
        };
        /** ProfileProbe */
        ProfileProbe: {
            /**
             * Api Key
             * @default
             */
            api_key?: string;
            /** Base Url */
            base_url?: string | null;
            /** Context Window Tokens */
            context_window_tokens?: number | null;
            /** Max Output Tokens */
            max_output_tokens?: number | null;
            /**
             * Model
             * @default gpt-4o-mini
             */
            model?: string;
            /** Parameter Mode */
            parameter_mode?: string | null;
            /** Profile Id */
            profile_id?: string | null;
            /** Reasoning Effort */
            reasoning_effort?: string | null;
        };
        /** ProfileUpdate */
        ProfileUpdate: {
            /** Api Key */
            api_key?: string | null;
            /** Base Url */
            base_url?: string | null;
            /** Context Window Tokens */
            context_window_tokens?: number | null;
            /** Is Default */
            is_default?: boolean | null;
            /** Max Output Tokens */
            max_output_tokens?: number | null;
            /** Model */
            model?: string | null;
            /** Name */
            name?: string | null;
            /** Parameter Mode */
            parameter_mode?: string | null;
            /** Reasoning Effort */
            reasoning_effort?: string | null;
            /** Temperature */
            temperature?: number | null;
        };
        /** Project */
        Project: {
            /**
             * Corpus Count
             * @default 0
             */
            corpus_count?: number;
            /** Created At */
            created_at?: string | null;
            /**
             * Description
             * @default
             */
            description?: string;
            /** Id */
            id: string;
            /**
             * Included Source Count
             * @default 0
             */
            included_source_count?: number;
            /** Name */
            name: string;
            /** Owner Id */
            owner_id: string;
            /**
             * Source Count
             * @default 0
             */
            source_count?: number;
            /** Updated At */
            updated_at?: string | null;
        };
        /** ProjectSummary */
        ProjectSummary: {
            /**
             * Corpus Count
             * @default 0
             */
            corpus_count?: number;
            /** Created At */
            created_at?: string | null;
            /**
             * Description
             * @default
             */
            description?: string;
            /** Id */
            id: string;
            /**
             * Included Source Count
             * @default 0
             */
            included_source_count?: number;
            /** Name */
            name: string;
            /** Owner Id */
            owner_id: string;
            /**
             * Source Count
             * @default 0
             */
            source_count?: number;
            /** Updated At */
            updated_at?: string | null;
        };
        /** PromptPreviewRequest */
        PromptPreviewRequest: {
            /** Behavior */
            behavior: string;
            /**
             * Prompt Mode
             * @default append
             */
            prompt_mode?: string;
            /**
             * System Prompt
             * @default
             */
            system_prompt?: string;
        };
        /**
         * ProseBlock
         * @description LLM 写的叙述段落，含 ``[n]`` 角标。模型只拥有这一种块。
         */
        ProseBlock: {
            /**
             * Kind
             * @default prose
             * @constant
             */
            kind?: "prose";
            /**
             * Markdown
             * @default
             */
            markdown?: string;
        };
        /**
         * QualityMetrics
         * @description Deterministic run metrics; no judge model is involved.
         */
        QualityMetrics: {
            /**
             * Blocked Sources
             * @default 0
             */
            blocked_sources?: number;
            /**
             * Cited Source Snapshot Coverage
             * @default 0
             */
            cited_source_snapshot_coverage?: number;
            /**
             * Cited Sources
             * @default 0
             */
            cited_sources?: number;
            /**
             * Conflicted
             * @default 0
             */
            conflicted?: number;
            /**
             * Corroborated
             * @default 0
             */
            corroborated?: number;
            /**
             * Disputed
             * @default 0
             */
            disputed?: number;
            /**
             * Elapsed Seconds
             * @default 0
             */
            elapsed_seconds?: number;
            /**
             * Eligible Finding Rate
             * @default 0
             */
            eligible_finding_rate?: number;
            /**
             * Independent Publishers
             * @default 0
             */
            independent_publishers?: number;
            /**
             * Report Eligible
             * @default 0
             */
            report_eligible?: number;
            /**
             * Semantically Supported
             * @default 0
             */
            semantically_supported?: number;
            /**
             * Source Snapshots
             * @default 0
             */
            source_snapshots?: number;
            /**
             * Supported Finding Rate
             * @default 0
             */
            supported_finding_rate?: number;
            /**
             * Total Findings
             * @default 0
             */
            total_findings?: number;
            /**
             * Total Tokens
             * @default 0
             */
            total_tokens?: number;
            /**
             * Verbatim Verified
             * @default 0
             */
            verbatim_verified?: number;
            /**
             * Verified Finding Rate
             * @default 0
             */
            verified_finding_rate?: number;
        };
        /**
         * Quantity
         * @description 一条论断里的结构化数值。
         *
         *     科学论断的核心不是句子而是"某指标 = 某数值"，所以数值必须脱离自由文本单独
         *     建模——否则出对照表时只能从 statement 里正则抠数字，那等于把编造风险搬到了
         *     报告层。
         *
         *     ``rendered`` 保留原文写法（``38.36``），它决定容差口径：声明两位小数就按
         *     ±0.005 判，声明整数就按 ±0.5 判。丢掉它就只能拍一个固定 epsilon，
         *     "38.36 与 38.4 是否一致"这类问题会被判错。
         *
         *     ``comparator`` 区分"超过 35 dB"（下界）与"等于 35 dB"（点值）。吞掉它会让
         *     报告给出比证据更强的结论。
         */
        Quantity: {
            /**
             * Comparator
             * @default
             * @enum {string}
             */
            comparator?: "" | "=" | ">" | ">=" | "<" | "<=";
            /**
             * Metric
             * @description 指标名，如 PSNR / SSIM / SAM / 参数量
             * @default
             */
            metric?: string;
            /**
             * Rendered
             * @description 原文中的数值写法，决定容差
             * @default
             */
            rendered?: string;
            /**
             * Uncertainty
             * @description ± 值（标准差 / 置信区间半宽）
             */
            uncertainty?: number | null;
            /**
             * Unit
             * @description 原文单位写法，如 dB / M / nm；无单位留空
             * @default
             */
            unit?: string;
            /** Value */
            value?: number | null;
        };
        /**
         * ReferenceEntry
         * @description 参考来源一条。``reference`` 是学术引用文本，为空则回退裸 ``url``。
         */
        ReferenceEntry: {
            /** Index */
            index: number;
            /**
             * Reference
             * @default
             */
            reference?: string;
            /** Url */
            url: string;
        };
        /** Report */
        Report: {
            /**
             * Citations
             * @description 按 [n] 顺序排列的来源 URL
             */
            citations?: string[];
            /** Markdown */
            markdown: string;
            /** Query */
            query: string;
        };
        /**
         * ReportDocument
         * @description 一份报告的完整结构。所有导出渲染器都只消费它。
         *
         *     ``Report``（``{query, markdown, citations}``）**继续保留且语义不变**：前端按
         *     下标做 [n] 跳转、质量指标按 URL 与检索快照比对覆盖率，都依赖它。本模型是
         *     并列的增量产物，不是替代品——旧客户端与历史 run 完全不受影响。
         */
        ReportDocument: {
            /**
             * Abstract
             * @default
             */
            abstract?: string;
            /** Authors */
            authors?: string[];
            /** Blocks */
            blocks?: (components["schemas"]["ProseBlock"] | components["schemas"]["TableBlock"] | components["schemas"]["ChartBlock"])[];
            /**
             * Disclaimer
             * @default 本报告展示的是检索服务返回的快照上下文，不等同于来源完整正文，也不等同于事实已获证实。系统保证的是出处可追溯、引用可逐字核验、单源/双源/冲突状态可判定；不保证论断在开放世界为真。证据标签针对输入素材，正文的引用与数值检查不等同于逐段语义审核。
             */
            disclaimer?: string;
            /** Evidence */
            evidence?: components["schemas"]["EvidenceRecord"][];
            final_validation?: components["schemas"]["FinalReportValidation"] | null;
            /**
             * Institution
             * @default
             */
            institution?: string;
            /** Keywords */
            keywords?: string[];
            overview?: components["schemas"]["Overview"];
            /**
             * Query
             * @default
             */
            query?: string;
            /** References */
            references?: components["schemas"]["ReferenceEntry"][];
            /**
             * Schema Version
             * @default 1
             */
            schema_version?: number;
            /** Sections */
            sections?: components["schemas"]["PaperSection"][];
            /**
             * Title
             * @default
             */
            title?: string;
        };
        /**
         * ResearchParams
         * @description per-run 研究参数覆盖（缺省字段沿用服务端 Settings）。
         */
        ResearchParams: {
            /** Max Concurrency */
            max_concurrency?: number | null;
            /** Max Rounds */
            max_rounds?: number | null;
            /** Max Run Seconds */
            max_run_seconds?: number | null;
            /** Max Sub Questions */
            max_sub_questions?: number | null;
            /** Require Corroboration */
            require_corroboration?: boolean | null;
            /** Results Per Search */
            results_per_search?: number | null;
        };
        /** ResearchResult */
        ResearchResult: {
            /** Findings */
            findings?: components["schemas"]["Finding"][];
            /** Sub Question */
            sub_question: string;
        };
        /**
         * RunDetail
         * @description 单次研究详情（含计划、结果、报告）。
         */
        RunDetail: {
            /** Created At */
            created_at?: string | null;
            /**
             * Elapsed
             * @default 0
             */
            elapsed?: number;
            /** Events */
            events?: components["schemas"]["Event"][];
            /** Id */
            id: string;
            intent?: components["schemas"]["IntentDecision"] | null;
            /**
             * Interpretation
             * @default
             */
            interpretation?: string;
            manifest?: components["schemas"]["RunManifest"] | null;
            metrics?: components["schemas"]["QualityMetrics"] | null;
            orchestration?: components["schemas"]["WorkflowRun"] | null;
            /** Owner Id */
            owner_id?: string | null;
            /** Project Id */
            project_id?: string | null;
            /** Query */
            query: string;
            report?: components["schemas"]["Report"] | null;
            /** Results */
            results?: components["schemas"]["ResearchResult"][];
            /** Sources */
            sources?: components["schemas"]["Source"][];
            /** Status */
            status: string;
            /** Sub Questions */
            sub_questions?: components["schemas"]["SubQuestion"][];
            /** Tags */
            tags?: string[];
            /**
             * Total Tokens
             * @default 0
             */
            total_tokens?: number;
        };
        /**
         * RunManifest
         * @description Non-secret inputs that make one research run reproducible.
         */
        RunManifest: {
            /** Catalog Model Profiles */
            catalog_model_profiles?: {
                [key: string]: unknown;
            }[];
            /**
             * Catalog Snapshot Hash
             * @default
             */
            catalog_snapshot_hash?: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
            /**
             * Llm Endpoint
             * @default
             */
            llm_endpoint?: string;
            /**
             * Llm Model
             * @default
             */
            llm_model?: string;
            /** Query Hash */
            query_hash: string;
            /**
             * Schema Version
             * @default 1
             */
            schema_version?: number;
            /**
             * Search Backend
             * @default
             */
            search_backend?: string;
            /** Settings */
            settings?: {
                [key: string]: boolean | number | string | string[] | {
                    [key: string]: boolean | number;
                } | null;
            };
            /** Workflow Hash */
            workflow_hash: string;
            /** Workflow Name */
            workflow_name: string;
        };
        /**
         * RunStatus
         * @enum {string}
         */
        RunStatus: "pending" | "running" | "succeeded" | "failed" | "cancelled";
        /**
         * RunSummary
         * @description 历史列表行（轻量）。
         */
        RunSummary: {
            /** Created At */
            created_at?: string | null;
            /**
             * Elapsed
             * @default 0
             */
            elapsed?: number;
            /** Id */
            id: string;
            /** Owner Id */
            owner_id?: string | null;
            /** Project Id */
            project_id?: string | null;
            /** Query */
            query: string;
            /** Status */
            status: string;
            /** Tags */
            tags?: string[];
            /**
             * Total Tokens
             * @default 0
             */
            total_tokens?: number;
        };
        /**
         * ScholarlyMetadata
         * @description 一条学术来源的出处元数据。
         *
         *     存在的理由是「出处」在科学场景里不等于 URL：同一篇工作常同时存在预印本、
         *     期刊版与机构库三个 URL，而真正可引用、可去重、可判独立性的标识是 DOI 与
         *     ``work_id``。字段全部可选且默认空——抽不到就是抽不到，绝不补默认值，
         *     因为一个编造出来的年份或期刊比缺失更糟。
         *
         *     ``peer_reviewed`` 刻意用三态 ``bool | None``：预印本平台缺少 journal_ref
         *     并不等于「未经评审」，只等于「不知道」。把未知压成 False 会让报告给出
         *     自己并不掌握的结论。
         */
        ScholarlyMetadata: {
            /** Affiliations */
            affiliations?: string[];
            /** Authors */
            authors?: string[];
            /** Citation Count */
            citation_count?: number | null;
            /**
             * Doi
             * @default
             */
            doi?: string;
            /**
             * Oa Pdf Url
             * @description 可合法获取的开放全文 PDF；全文解析阶段消费
             * @default
             */
            oa_pdf_url?: string;
            /**
             * Peer Reviewed
             * @description None＝未知，不是 False
             */
            peer_reviewed?: boolean | null;
            /** Referenced Works */
            referenced_works?: string[];
            /** Retracted */
            retracted?: boolean | null;
            /**
             * Section
             * @description 该证据取自哪一节；全文分节后填充
             * @default
             */
            section?: string;
            /**
             * Venue
             * @default
             */
            venue?: string;
            /**
             * Version
             * @description 预印本版本号，如 arXiv 的 v2
             * @default
             */
            version?: string;
            /**
             * Work Id
             * @description OpenAlex / S2 的同一工作聚类 ID，用于跨库判定同一篇
             * @default
             */
            work_id?: string;
            /** Year */
            year?: number | null;
        };
        /**
         * SearchKeyView
         * @description 搜索 key 对外视图：api_key 脱敏。
         */
        SearchKeyView: {
            /** Api Key Hint */
            api_key_hint: string;
            /**
             * Enabled
             * @default true
             */
            enabled?: boolean;
            /** Id */
            id: string;
            /**
             * Label
             * @default
             */
            label?: string;
            /**
             * Priority
             * @default 0
             */
            priority?: number;
            /**
             * Provider
             * @default tavily
             */
            provider?: string;
        };
        /** SearchProfileInput */
        SearchProfileInput: {
            /**
             * Enabled
             * @default true
             */
            enabled?: boolean;
            /**
             * Endpoint
             * @default
             */
            endpoint?: string;
            /** Key Ids */
            key_ids?: string[];
            /**
             * Model
             * @default
             */
            model?: string;
            /** Name */
            name: string;
            /**
             * Provider
             * @enum {string}
             */
            provider: "tavily" | "brave" | "serper" | "grok" | "openalex" | "arxiv" | "responses" | "chat_search";
        };
        /** SearchProfileView */
        SearchProfileView: {
            /**
             * Builtin
             * @default false
             */
            builtin?: boolean;
            /**
             * Enabled
             * @default true
             */
            enabled?: boolean;
            /**
             * Endpoint
             * @default
             */
            endpoint?: string;
            /** Id */
            id: string;
            /** Key Ids */
            key_ids?: string[] | null;
            /**
             * Key Order Frozen
             * @default false
             */
            key_order_frozen?: boolean;
            /**
             * Model
             * @default
             */
            model?: string;
            /** Name */
            name: string;
            /**
             * Provider
             * @enum {string}
             */
            provider: "tavily" | "brave" | "serper" | "grok" | "openalex" | "arxiv" | "responses" | "chat_search";
        };
        /** Source */
        Source: {
            /**
             * Content
             * @default
             */
            content?: string;
            /**
             * Content Hash
             * @default
             */
            content_hash?: string;
            /**
             * Locator
             * @default
             */
            locator?: string;
            scholarly?: components["schemas"]["ScholarlyMetadata"] | null;
            /**
             * Title
             * @default
             */
            title?: string;
            /** Url */
            url: string;
        };
        /** SourceChunk */
        SourceChunk: {
            /** Content */
            content: string;
            /** Content Hash */
            content_hash: string;
            /**
             * End Char
             * @default 0
             */
            end_char?: number;
            /** Id */
            id: string;
            /** Locator */
            locator: string;
            /** Ordinal */
            ordinal: number;
            /** Page End */
            page_end?: number | null;
            /** Page Start */
            page_start?: number | null;
            /**
             * Section
             * @default
             */
            section?: string;
            /** Source Id */
            source_id: string;
            /**
             * Start Char
             * @default 0
             */
            start_char?: number;
        };
        /**
         * SourceIdentity
         * @description 判定"是否同一发布方"所需的最小信息集。
         *
         *     为什么要把它denormalize 到 Finding 上：交叉印证判定在 ``ClaimConsistencyVerifier``
         *     里跨全部子问题的结果进行，那时手里只有 ``Finding``，早已没有 ``Source``。而
         *     ``EvidenceVerifier.verify`` 是唯一同时握有两者的时刻——``source_title`` /
         *     ``source_reference`` / ``source_content_hash`` 已经是按这个理由存下来的。
         *
         *     存**原始值**而不是预先归一化的键：归一化规则将来会改进（作者名罗马化变体、
         *     标题差异），存原始值意味着历史 run 重新判定时也能受益于改进后的规则。
         *
         *     刻意不含机构（``affiliations``）：同一机构不等于同一团队，大学里两个互不相关的
         *     组报同一个数是真的独立验证。"同一团队"这个真正的信号由作者重叠覆盖。
         */
        SourceIdentity: {
            /** Authors */
            authors?: string[];
            /**
             * Doi
             * @default
             */
            doi?: string;
            /**
             * Domain
             * @description registrable domain，兜底判据与向后兼容
             * @default
             */
            domain?: string;
            /** Peer Reviewed */
            peer_reviewed?: boolean | null;
            /** Retracted */
            retracted?: boolean | null;
            /**
             * Section
             * @description 证据取自全文哪一节
             * @default
             */
            section?: string;
            /**
             * Title
             * @default
             */
            title?: string;
            /**
             * Work Id
             * @description OpenAlex / arXiv 的同一工作标识
             * @default
             */
            work_id?: string;
        };
        /** StepRun */
        StepRun: {
            /**
             * Agent
             * @default
             */
            agent?: string;
            /**
             * Attempt
             * @default 0
             */
            attempt?: number;
            /** Error */
            error?: string | null;
            /** Finished At */
            finished_at?: string | null;
            /** Id */
            id?: string;
            /** Kind */
            kind: string;
            /** Label */
            label: string;
            /** Node Id */
            node_id: string;
            /** Started At */
            started_at?: string | null;
            /** @default pending */
            status?: components["schemas"]["StepStatus"];
        };
        /**
         * StepStatus
         * @enum {string}
         */
        StepStatus: "pending" | "ready" | "running" | "retrying" | "succeeded" | "failed" | "skipped" | "cancelled";
        /** SubQuestion */
        SubQuestion: {
            /**
             * Depends On
             * @description 依赖的前驱子问题序号（本计划内 0 起始下标）；为空表示无依赖、可立即并行检索
             */
            depends_on?: number[];
            /**
             * Question
             * @description 一个可独立检索的子问题
             */
            question: string;
            /**
             * Rationale
             * @description 为什么需要研究它
             * @default
             */
            rationale?: string;
        };
        /**
         * TableBlock
         * @description 一张对照表。行是被比较的对象，列是维度。
         *
         *     ``notes`` 是协议脚注。它在科学报告里不是装饰：同一个 PSNR 数字在 28 波段与
         *     31 波段、256×256 裁剪与全图、不同 mask 下并不可比，抄了数字不抄口径就是误导。
         */
        TableBlock: {
            /**
             * Caption
             * @default
             */
            caption?: string;
            /** Columns */
            columns?: components["schemas"]["TableColumn"][];
            /** Id */
            id: string;
            /**
             * Kind
             * @default table
             * @constant
             */
            kind?: "table";
            /** Notes */
            notes?: string[];
            /** Rows */
            rows?: components["schemas"]["TableRow"][];
            /**
             * Title
             * @default
             */
            title?: string;
        };
        /**
         * TableCell
         * @description 一个单元格。
         *
         *     ``value`` 为空即"未报告"——渲染器必须原样呈现缺失，禁止补零或让模型填补。
         *     ``numeric`` 与 ``value`` 分开保存：前者给图表算坐标，后者是给人看的显示形式
         *     （含有效数字与正负号），两者不能互相推导。
         */
        TableCell: {
            /** Citations */
            citations?: number[];
            /**
             * Disputed
             * @default false
             */
            disputed?: boolean;
            /**
             * Note Ref
             * @description 协议脚注编号
             */
            note_ref?: number | null;
            /** Numeric */
            numeric?: number | null;
            /**
             * Value
             * @default
             */
            value?: string;
        };
        /**
         * TableColumn
         * @description 一列的元数据。``unit`` 与 ``note`` 分开，是因为单位属于列，脚注属于口径。
         */
        TableColumn: {
            /**
             * Align
             * @default left
             * @enum {string}
             */
            align?: "left" | "right";
            /** Key */
            key: string;
            /** Label */
            label: string;
            /** Note Ref */
            note_ref?: number | null;
            /**
             * Numeric
             * @default false
             */
            numeric?: boolean;
            /**
             * Unit
             * @default
             */
            unit?: string;
        };
        /** TableRow */
        TableRow: {
            /** Cells */
            cells?: {
                [key: string]: components["schemas"]["TableCell"];
            };
            /** Citation */
            citation?: number | null;
            /** Label */
            label: string;
        };
        /**
         * TagCount
         * @description 标签 + 引用计数（历史筛选侧栏用）。
         */
        TagCount: {
            /** Count */
            count: number;
            /** Tag */
            tag: string;
        };
        /** TagsUpdate */
        TagsUpdate: {
            /** Tags */
            tags?: string[];
        };
        /**
         * TestResult
         * @description 「测试连接」结果：ok=是否可用、latency_ms=往返耗时、detail=失败原因。
         */
        TestResult: {
            /**
             * Detail
             * @default
             */
            detail?: string;
            /** Latency Ms */
            latency_ms: number;
            /** Ok */
            ok: boolean;
        };
        /** UpdateSourceRequest */
        UpdateSourceRequest: {
            /**
             * Status
             * @enum {string}
             */
            status: "included" | "excluded";
        };
        /** ValidationError */
        ValidationError: {
            /** Context */
            ctx?: Record<string, never>;
            /** Input */
            input?: unknown;
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
        };
        /** WorkflowDefCreate */
        WorkflowDefCreate: {
            /**
             * Description
             * @default
             */
            description?: string;
            /**
             * Display Name
             * @default
             */
            display_name?: string;
            /** Edges */
            edges?: {
                [key: string]: unknown;
            }[];
            /**
             * Enabled
             * @default true
             */
            enabled?: boolean;
            /** Name */
            name: string;
            /** Nodes */
            nodes?: {
                [key: string]: unknown;
            }[];
            /** Steps */
            steps?: {
                [key: string]: unknown;
            }[];
            /**
             * Version
             * @default 1
             */
            version?: number;
            /** Viewport */
            viewport?: {
                [key: string]: unknown;
            };
        };
        /** WorkflowDefUpdate */
        WorkflowDefUpdate: {
            /** Description */
            description?: string | null;
            /** Display Name */
            display_name?: string | null;
            /** Edges */
            edges?: {
                [key: string]: unknown;
            }[] | null;
            /** Enabled */
            enabled?: boolean | null;
            /** Nodes */
            nodes?: {
                [key: string]: unknown;
            }[] | null;
            /** Steps */
            steps?: {
                [key: string]: unknown;
            }[] | null;
            /** Version */
            version?: number | null;
            /** Viewport */
            viewport?: {
                [key: string]: unknown;
            } | null;
        };
        /**
         * WorkflowDefView
         * @description 自定义工作流对外视图（无密钥，steps 原样透出供前端编辑/后端运行）。
         */
        WorkflowDefView: {
            /**
             * Description
             * @default
             */
            description?: string;
            /**
             * Display Name
             * @default
             */
            display_name?: string;
            /** Edges */
            edges?: {
                [key: string]: unknown;
            }[];
            /**
             * Enabled
             * @default true
             */
            enabled?: boolean;
            /** Id */
            id: string;
            /** Name */
            name: string;
            /** Nodes */
            nodes?: {
                [key: string]: unknown;
            }[];
            /** Steps */
            steps?: {
                [key: string]: unknown;
            }[];
            /**
             * Version
             * @default 1
             */
            version?: number;
            /** Viewport */
            viewport?: {
                [key: string]: unknown;
            };
        };
        /** WorkflowRun */
        WorkflowRun: {
            /**
             * Attempt
             * @default 1
             */
            attempt?: number;
            /** Checkpoint */
            checkpoint?: {
                [key: string]: unknown;
            };
            /** Definition */
            definition?: {
                [key: string]: unknown;
            };
            /** Finished At */
            finished_at?: string | null;
            /** Id */
            id?: string;
            /** Input */
            input?: {
                [key: string]: unknown;
            };
            /** Output */
            output?: {
                [key: string]: unknown;
            };
            /** Started At */
            started_at?: string | null;
            /** @default pending */
            status?: components["schemas"]["RunStatus"];
            /** Steps */
            steps?: components["schemas"]["StepRun"][];
            /** Workflow Name */
            workflow_name: string;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    index__get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "text/html": string;
                };
            };
        };
    };
    list_agents_api_agents_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AgentCardView"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_agent_api_agents_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AgentCardCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AgentCardView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preview_role_prompt_api_agents_prompt_preview_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["PromptPreviewRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_agent_api_agents__agent_id__put: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                agent_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AgentCardUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AgentCardView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_agent_api_agents__agent_id__delete: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                agent_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    upload_attachment_api_attachments_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AttachmentUpload"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_behaviors_api_behaviors_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": string[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_capabilities_api_capabilities_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: {
                            [key: string]: boolean;
                        };
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_config_api_config_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConfigView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_config_api_config_put: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ConfigUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ConfigView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_quality_schema_api_config_quality_schema_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    parse_dataset_upload_api_datasets_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["DatasetUpload"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    assess_intent_api_intent_assess_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AssessRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["AssessResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_models_api_models_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelProfileView"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_model_api_models_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProfileCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelProfileView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    discover_models_api_models_discover_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProfileProbe"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelDiscoveryResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_model_config_api_models_test_config_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProfileProbe"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_model_api_models__profile_id__put: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                profile_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ProfileUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ModelProfileView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_model_api_models__profile_id__delete: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                profile_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_model_api_models__profile_id__test_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                profile_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_projects_api_projects_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ProjectSummary"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_project_api_projects_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateProjectRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Project"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_project_api_projects__project_id__get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Project"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_corpora_api_projects__project_id__corpora_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Corpus"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_corpus_api_projects__project_id__corpora_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateCorpusRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Corpus"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_sources_api_projects__project_id__sources_get: {
        parameters: {
            query?: {
                corpus_id?: string | null;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LibrarySource"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    import_source_api_projects__project_id__sources_import_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                project_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ImportSourceRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LibrarySource"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_source_api_projects__project_id__sources__source_id__delete: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                project_id: string;
                source_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_source_api_projects__project_id__sources__source_id__patch: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                project_id: string;
                source_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["UpdateSourceRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LibrarySource"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_source_chunks_api_projects__project_id__sources__source_id__chunks_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                project_id: string;
                source_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SourceChunk"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_conversations_api_qa_conversations_get: {
        parameters: {
            query?: {
                run_id?: string | null;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_conversation_api_qa_conversations_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateConversation"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_conversation_api_qa_conversations__conversation_id__get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                conversation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_conversation_api_qa_conversations__conversation_id__delete: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                conversation_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    ask_api_qa_conversations__conversation_id__messages_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                conversation_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AskRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    ask_stream_api_qa_conversations__conversation_id__messages_stream_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                conversation_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["AskRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    request_status_api_qa_conversations__conversation_id__requests__request_id__get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                conversation_id: string;
                request_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    research_api_research_get: {
        parameters: {
            query: {
                /** @description 研究问题 */
                q: string;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resource_preflight_api_resource_preflight_get: {
        parameters: {
            query?: {
                workflow?: string;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_roles_api_roles_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_runs_api_runs_get: {
        parameters: {
            query?: {
                limit?: number;
                offset?: number;
                status?: string | null;
                q?: string | null;
                tag?: string | null;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunSummary"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_run_api_runs_post: {
        parameters: {
            query?: never;
            header?: {
                "Idempotency-Key"?: string | null;
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["CreateRunRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CreateRunResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    batch_delete_api_runs_batch_delete_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["BatchDeleteRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["BatchDeleteResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_api_runs__run_id__get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_run_api_runs__run_id__delete: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    cancel_run_api_runs__run_id__cancel_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CancelRunResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_deliverables_api_runs__run_id__deliverables_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    download_deliverable_api_runs__run_id__deliverables__name__get: {
        parameters: {
            query?: {
                version?: string | null;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
                name: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_document_api_runs__run_id__document_get: {
        parameters: {
            query?: {
                include_hsi_tables?: boolean;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReportDocument"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_document_bib_api_runs__run_id__document_bib_get: {
        parameters: {
            query?: {
                include_hsi_tables?: boolean;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_document_bundle_api_runs__run_id__document_bundle_zip_get: {
        parameters: {
            query?: {
                profile?: string;
                template?: string;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_document_csv_api_runs__run_id__document_csv_get: {
        parameters: {
            query?: {
                table_id?: string | null;
                include_hsi_tables?: boolean;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_document_markdown_api_runs__run_id__document_md_get: {
        parameters: {
            query?: {
                include_hsi_tables?: boolean;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_document_paper_pdf_api_runs__run_id__document_paper_pdf_get: {
        parameters: {
            query?: {
                profile?: string;
                template?: string;
                include_hsi_tables?: boolean;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_document_pdf_api_runs__run_id__document_pdf_get: {
        parameters: {
            query?: {
                include_hsi_tables?: boolean;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_document_latex_api_runs__run_id__document_tex_get: {
        parameters: {
            query?: {
                profile?: string;
                template?: string;
                include_hsi_tables?: boolean;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_document_xlsx_api_runs__run_id__document_xlsx_get: {
        parameters: {
            query?: {
                table_id?: string | null;
                include_hsi_tables?: boolean;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_events_api_runs__run_id__events_get: {
        parameters: {
            query?: {
                after_seq?: number;
                limit?: number;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Event"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_narrative_api_runs__run_id__narrative_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_reader_api_runs__run_id__reader_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_reader_pdf_api_runs__run_id__reader__document_id__pdf_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
                document_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    resume_run_api_runs__run_id__resume_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            202: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CreateRunResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    stream_run_api_runs__run_id__stream_get: {
        parameters: {
            query?: never;
            header?: {
                "Last-Event-ID"?: string | null;
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    set_tags_api_runs__run_id__tags_put: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["TagsUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["RunDetail"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_run_template_api_runs__run_id__template_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_workspace_api_runs__run_id__workspace_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_workspace_file_api_runs__run_id__workspace_file_get: {
        parameters: {
            query: {
                path: string;
            };
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                run_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_keys_api_search_keys_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SearchKeyView"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_key_api_search_keys_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KeyCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SearchKeyView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_key_api_search_keys__key_id__put: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                key_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["KeyUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SearchKeyView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_key_api_search_keys__key_id__delete: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                key_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_key_api_search_keys__key_id__test_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                key_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_search_profiles_api_search_profiles_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SearchProfileView"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_search_profile_api_search_profiles_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SearchProfileInput"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SearchProfileView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_search_profile_api_search_profiles__profile_id__put: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                profile_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["SearchProfileInput"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["SearchProfileView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_search_profile_api_search_profiles__profile_id__delete: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                profile_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    test_search_profile_api_search_profiles__profile_id__test_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                profile_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TestResult"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    search_resource_impact_api_search_resources_impact_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_tags_api_tags_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["TagCount"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_templates_api_templates_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    preview_contract_api_templates_contract_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["ContractPreviewRequest"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_tiers_api_tiers_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_usage_api_usage_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: unknown;
                    };
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_workflows_api_workflows_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    }[];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_custom_workflows_api_workflows_custom_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WorkflowDefView"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    create_custom_workflow_api_workflows_custom_post: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkflowDefCreate"];
            };
        };
        responses: {
            /** @description Successful Response */
            201: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WorkflowDefView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    update_custom_workflow_api_workflows_custom__workflow_id__put: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                workflow_id: string;
            };
            cookie?: never;
        };
        requestBody: {
            content: {
                "application/json": components["schemas"]["WorkflowDefUpdate"];
            };
        };
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["WorkflowDefView"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    delete_custom_workflow_api_workflows_custom__workflow_id__delete: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path: {
                workflow_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            204: {
                headers: {
                    [name: string]: unknown;
                };
                content?: never;
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    healthz_healthz_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
        };
    };
    livez_livez_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
        };
    };
    metrics_endpoint_metrics_get: {
        parameters: {
            query?: never;
            header?: {
                "x-api-key"?: string | null;
                authorization?: string | null;
            };
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": unknown;
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    readyz_readyz_get: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": {
                        [key: string]: string;
                    };
                };
            };
        };
    };
}
