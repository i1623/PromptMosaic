<!-- Hero image will be added later. -->
<!-- ![PromptMosaic](docs/images/hero.png) -->

# PromptMosaic

[Japanese](README.md) | [English](README_EN.md)

**PromptMosaic** is a local prompt management and generation GUI for image generation AI [InvokeAI](https://github.com/invoke-ai/InvokeAI).
It lets you build prompts as tiles, follow generation lineage in a history tree, and run multiple models in rotation. Image generation itself is performed by InvokeAI.

- **Tile-based prompt editing** - arrange, emphasize, enable, and disable words or text snippets as tiles.
- **Generation lineage / parallel-world history map** - visualize which generations branched from which results and jump back to any past point.
- **Multi-model plans** - cycle through multiple models, LoRAs, and generation parameters in one generation run.
- **11 languages** - Japanese, English, Chinese (Simplified / Traditional), Korean, German, French, Spanish, Italian, Portuguese (Brazil), and Russian.
- **Simple data backup** - back up PromptMosaic by copying the entire `data` folder.

> **Version:** 1.4.0
> **Target InvokeAI:** 6.13 or later
> **Supported OS:** Windows 11 (PySide6 / Python 3.10-3.12)

---

## Documentation

| Document | Contents |
| --- | --- |
| **[Tutorial](docs/TUTORIAL_EN.md)** | Install, connect to InvokeAI, and generate the first image |
| **[Manual](docs/MANUAL_EN.md)** | Full feature reference and screen-by-screen operation guide |

---

## Quick Start

```bat
:: 1. Install dependencies (first run only)
install_windows.bat

:: 2. Launch PromptMosaic
PromptMosaic.bat
```

On first launch, the **InvokeAI Data Acquisition** wizard opens. Start InvokeAI 6.13 or later, then follow the wizard to fetch models, LoRAs, and generation templates from your current InvokeAI environment. See the [Tutorial](docs/TUTORIAL_EN.md) for details.

PromptMosaic is designed for a regular Python virtual environment. It does not require Conda or Anaconda, and the launcher avoids using Conda DLL paths when they are present on the machine.

---

## License

PromptMosaic is released under the **[MIT License](LICENSE)**.

- Forking, modification, redistribution, and commercial use are allowed, including use inside closed-source products.
- When redistributing, include the copyright notice and full license text.
- This software is provided without warranty.

```text
Copyright (c) 2026 i1623
```

> Third-party projects and libraries used with PromptMosaic, including InvokeAI, Qt, and Python packages, are governed by their own licenses. When redistributing, review the license terms for the dependencies listed in `requirements.txt`. Qt (PySide6) is especially subject to LGPL/GPL/commercial licensing terms.

---

## Acknowledgments

PromptMosaic is built on many excellent open-source projects. Deep thanks to their authors and communities.

### InvokeAI

PromptMosaic does not generate images by itself. All actual image generation is handled by **[InvokeAI](https://github.com/invoke-ai/InvokeAI)**, developed by [Invoke](https://www.invoke.com/) and the InvokeAI community. PromptMosaic fetches InvokeAI txt2img workflow graphs as **generation templates**, replaces only the prompt, seed, and known parameter fields, and submits the result to the InvokeAI queue.

This application would not exist without the long-running work of the InvokeAI developers and contributors. Prompt weighting syntax follows the **[Compel](https://github.com/damian0815/compel)** style used by InvokeAI.

### UI Framework and Color Theme

| Project | Purpose | Author / Provider |
| --- | --- | --- |
| **[Qt for Python (PySide6 / shiboken6)](https://www.qt.io/qt-for-python)** | GUI framework | The Qt Company |
| **[Catppuccin](https://github.com/catppuccin/catppuccin)** | Theme colors (Mocha / Latte) | Catppuccin org |

### Python Libraries

| Library | Purpose | Author / Provider | License |
| --- | --- | --- | --- |
| **[Pillow](https://github.com/python-pillow/Pillow)** | PNG metadata parsing and image processing | Jeffrey A. Clark and contributors | MIT-CMU (HPND) |
| **[httpx](https://github.com/encode/httpx)** / **[httpcore](https://github.com/encode/httpcore)** | HTTP communication with InvokeAI / LLMs | Encode (Tom Christie and others) | BSD-3-Clause |
| **[h11](https://github.com/python-hyper/h11)** | HTTP/1.1 protocol | Nathaniel J. Smith | MIT |
| **[anyio](https://github.com/agronholm/anyio)** | Async I/O abstraction | Alex Gronholm | MIT |
| **[certifi](https://github.com/certifi/python-certifi)** | Root certificates | Kenneth Reitz / PSF | MPL-2.0 |
| **[idna](https://github.com/kjd/idna)** | Internationalized domain names | Kim Davies | BSD-3-Clause |
| **[exceptiongroup](https://github.com/agronholm/exceptiongroup)** | Backport for exception groups | Alex Gronholm | MIT / PSF |
| **[typing_extensions](https://github.com/python/typing_extensions)** | Type hint extensions | Python core team | PSF |

### Optional Integration

- **[LM Studio](https://lmstudio.ai/)** - optional local LLM server for prompt translation and automatic classification.

> When redistributing, follow the license terms of each dependency. Pay special attention to Qt licensing terms.

---

## Do Not Include in Public Packages

Do not include private developer data when publishing or redistributing PromptMosaic.

```text
.venv/                     Virtual environment
__pycache__/               Python caches
data/*.db / *.db-wal/-shm  Personal settings, prompts, history, and NSFW flags
data/template_cache*.json  Workflow graphs specific to an InvokeAI environment
data/thumbnails/           History thumbnails
data/model_thumbnails/     Model thumbnails
images/                    Local copies of generated images
```

Release packages should start with a clean state so PromptMosaic can create fresh databases on first launch.
