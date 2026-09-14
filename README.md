# iPaper

**A self-hosted AI workspace for papers and research**
Discover papers. Read deeply. Connect knowledge.

English · [中文](README_zh.md) · [Releases](https://github.com/ifzzh/iPaper/releases) · [Deployment](docs/operations/deployment.md) · [License](LICENSE)

iPaper continues development from PaperPilot and is adapting PaperQuay's reading-workspace design and related interactions into a unified web application. Its aim is to connect paper discovery, library management, reading, translation and research notes on infrastructure you control.

**iPaper uses the `ipaper` package and three dedicated image repositories, with `IPAPER_` configuration variables.** Existing installations must follow the documented migration; persistent identifiers and legacy environment-variable reads remain compatible.

## A connected research workflow

**Discover with Daily arXiv → collect papers → read and translate → ask questions → organize research.**

Existing services provide library management, BabelDOC translation, MinerU parsing and analysis, single-paper chat, Daily arXiv and local accounts. The unified library, continuous PDF reader, reading positions and sidebar chat are available. Release 1.2.0 adds dual translation results and verified source navigation; notes, library-operation agents, cross-paper retrieval, review writing and graphs are staged development work.

A version number or screen on the development branch does not establish release availability. Consult the target [Release](https://github.com/ifzzh/iPaper/releases) for shipped capabilities, known issues and verification. Planned features below are not presented as complete.


[Release 1.3.0](https://github.com/ifzzh/iPaper/releases/tag/v1.3.0) introduced separate AI overviews and detailed interpretations, shared original-text evidence, local/whole-paper question scopes, and Markdown/image-package/browser-print exports. Generation is explicit; existing results are read without paid requests. Unchanged Workers retain their verified 1.2.0 digests. See [single-paper understanding](docs/development/single-paper-understanding.md) for scope and limitations.

[Release 1.4.0](https://github.com/ifzzh/iPaper/releases/tag/v1.4.0) is deployed and verified. It adds full-document search, outlines and versioned bookmarks, cached paragraph translations in the original PDF, explicit selection translation, and figure/table/formula enlargement. No OCR or model request is triggered by searching or reading cached results. See [reading tools and limits](docs/development/reading-tools.md). The compatible matrix is Web 1.4.0 with Workers 1.2.0; use the final Release digests for deployment.

## Capabilities and direction

| Area | Available now | Further development |
| --- | --- | --- |
| Discovery | Daily arXiv, research topics, filtering, candidates and PDF asset processing | Discovery workflow refinements |
| Library | PaperQuay-inspired category/list/detail workspace; upload, search, favorites, Reading List and Zotero RDF import | Library-operation agents |
| Reading | Continuous PDFs, full-document search, outlines, bookmarks, figure enlargement, independent positions, verified sources and sidebar chat | Linked notes and annotations |
| Translation | BabelDOC mono/dual PDFs and independently stored structured bilingual blocks; bounded tasks, paragraph-cache viewing, explicit selection translation and block retries | Further language and layout refinements |
| Accounts and data | Local accounts, user-scoped data, server-side model settings and persistent tasks | Preserve existing papers, settings and chat history |
| Research | Independent AI overviews and detailed interpretations, source-backed local/whole-paper questions, offline analysis exports | Notes, library-operation agents, RAG, reviews and graphs |

The current product has one primary interface, with no requirement to choose between “new” and “old” versions. Experimental entry points in earlier releases are historical transition behavior, not the product goal.

## Two translation workflows

[Release 1.2.0](https://github.com/ifzzh/iPaper/releases/tag/v1.2.0) provides both workflows in the same reader. [Implementation and limitations](docs/development/dual-translation.md).

| Workflow | Process and result | Intended use |
| --- | --- | --- |
| **Layout translation · BabelDOC** | Produces translated or bilingual PDFs while aiming to preserve the original layout | Continuous reading, downloads and printing |
| **Structured translation · MinerU + an LLM** | MinerU extracts text, equations and tables; a model translates the resulting blocks | Paragraph comparison, source navigation, block retries and questions |

MinerU is the parser, not the translation engine. Both results are stored independently for the same paper. The reader switches between the original PDF, existing translated PDFs and structured text without automatically starting a paid translation task. Missing results require an explicit generation action.

The structure parser handles PDF segments of at most 200 pages, within the documented document and storage limits. Only validated coordinate schemas produce region highlights; other results use page references or explicitly unavailable navigation. Existing PDFs are never automatically translated again. Structured translation has its own server-side model configuration, visible scope and request budget. New data uses additive tables and immutable artifacts; [back up both](docs/operations/structured-backup.md) before upgrading.

## Self-hosting

The base deployment has three containers. Web serves the frontend assets, so a separate frontend container is unnecessary.

| Service | Responsibility | Docker Hub repository |
| --- | --- | --- |
| Web | UI, APIs, accounts, library, AI requests and task orchestration | [ifzzh520/ipaper](https://hub.docker.com/r/ifzzh520/ipaper) |
| Translation Worker | BabelDOC translation | [ifzzh520/ipaper-translation-worker](https://hub.docker.com/r/ifzzh520/ipaper-translation-worker) |
| Document Worker | Controlled PDF, archive and parser-output processing | [ifzzh520/ipaper-document-worker](https://hub.docker.com/r/ifzzh520/ipaper-document-worker) |

1. Select a published release and its compatible three-component matrix.
2. Follow the [deployment guide](docs/operations/deployment.md) to prepare Compose, storage, a local administrator and secret files.
3. Pin versions and digests, start the services and verify login, a real PDF and existing data.

Web uses port **7191** by default. Workers do not publish host ports. Unchanged Workers reuse verified versions rather than being rebuilt for every application release. [release-components.json](docker/release-components.json) describes the source checkout's target matrix; use the selected Release's matrix for deployment.

Local storage does not imply fully offline processing. Configured AI, translation or parsing services may receive document content and charge for requests. Choose providers and processing scope accordingly.

## Project origins

We thank the upstream projects and their contributors. iPaper builds on existing work and does not claim all inherited capabilities or referenced designs as original.

- **[PaperPilot](https://github.com/flyflypeng/PaperPilot)** is the direct code foundation, including library management, Daily arXiv, BabelDOC translation and paper analysis.
- **[PaperQuay](https://github.com/WangQrkkk/PaperQuay)** is an important reference for the reading workspace, library layout, notes and agent workflows. The current adaptation record describes independently implemented layouts and interactions; it does not claim a complete source-code or feature merger. See the [adaptation record](docs/development/paperquay-adaptation.md).
- **[Resophy](https://github.com/Mountchicken/Resophy)** is the earlier project credited by PaperPilot. We retain that attribution chain.

Core dependencies include [BabelDOC](https://github.com/funstory-ai/BabelDOC), [MinerU](https://github.com/opendatalab/MinerU), PDF.js, React and Flask. Dependencies retain their own licenses; attribution does not imply upstream endorsement.

## What's next

- Annotations and linked notes.
- Library-operation agents with inspectable plans and results.
- Retrieval across papers and notes, review writing and knowledge graphs.

Features are accepted through real behavior and interface verification, not placeholder screens or demonstration data. See [release documentation](docs/releases) and the [frontend development guide](frontend/README.md).

## License and contributing

The repository's current [LICENSE](LICENSE) is **CC BY-NC 4.0**. A historical MIT classifier remains inconsistent with that file and should not be interpreted as an MIT grant. PaperQuay declares **AGPL-3.0-only**; any source reuse and its notices must be assessed separately. Renaming the product does not change existing licensing terms.

Use [Issues](https://github.com/ifzzh/iPaper/issues) for reproducible bugs and feature requests. Include component versions, steps and sanitized errors; do not upload credentials, API keys or private papers. Contributions should identify referenced work and include relevant tests and documentation.
