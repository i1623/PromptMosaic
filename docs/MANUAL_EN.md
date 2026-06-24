# PromptMosaic Manual - Full Feature Reference

[Japanese](MANUAL.md) | [English](MANUAL_EN.md)

This document is a feature-by-feature reference for PromptMosaic.
If you are using PromptMosaic for the first time, read the [Tutorial](TUTORIAL_EN.md) first.

> Target version: **1.4.4** / Target Invoke: **6.13 or later**

---

## Table of Contents

- [Core Concepts](#core-concepts)
- [Window Layout](#window-layout)
- [Left Pane](#left-pane)
- [Center Pane](#center-pane)
- [Generation Bar](#generation-bar)
- [Right Pane](#right-pane)
- [Generation Lineage](#generation-lineage)
- [History Map](#history-map)
- [Multi-Model Plans](#multi-model-plans)
- [Generation Templates](#generation-templates)
- [Settings](#settings)
- [Data Management](#data-management)
- [Appendix](#appendix)

---

## Core Concepts

| Term | Meaning |
| --- | --- |
| Prompt | The text sent to Invoke for image generation |
| Tile | A reusable prompt piece such as a tag, word, or sentence |
| Bilingual display | A core PromptMosaic workflow: show English prompt text and local-language labels side by side |
| Translation assistance | Optional local LLM integration, such as LM Studio, for turning words or sentences in your language into English prompt tiles |
| Tag | A structured prompt word stored in the tag browser |
| Tile group | A reusable group of tiles |
| Prompt text | A longer sentence or paragraph stored for reuse |
| Generation history | Stored generation results, prompts, parameters, and thumbnails |
| History map | A lineage view showing how generations branched from earlier results |
| Generation template | An Invoke txt2img workflow graph saved as the base for future jobs |
| Multi-model plan | A sequence of models, LoRAs, and parameters used for repeated generation |

PromptMosaic is built around bilingual prompt editing, reusable prompt parts, and visible generation lineage. The goal is to make experimentation repeatable without losing either the English prompt text or the local-language meaning.

---

## Window Layout

PromptMosaic mainly uses three panes.

| Area | Role |
| --- | --- |
| Left pane | Browse tags, models, LoRAs, prompt text, and tile groups |
| Center pane | Edit the active prompt and generation parameters |
| Right pane | Review history, notes, groups, and deleted items |

> Screenshot placeholder: full main window
> `docs/images/main_window.png`

---

## Left Pane

### Tag Browser

The tag browser stores prompt words and categories.

Main operations:

- Add a tag to the current prompt.
- Search and filter tags.
- Organize tags with categories.
- Use local-language labels and prompt-language text separately.
- Show or hide NSFW items according to settings.

> Screenshot placeholder: tag browser
> `docs/images/tag_browser.png`

### Model Browser

The model browser lists models fetched from Invoke. You can select the base model for generation and check model information.

If the model list is empty, open the Invoke Data Acquisition wizard and fetch environment data again.

### LoRA Browser

The LoRA browser lists LoRAs fetched from Invoke.

Typical use:

- Add a LoRA to the active prompt or generation plan.
- Check LoRA names and base compatibility.
- Filter NSFW LoRAs when the NSFW display option is disabled.

### Prompt Text / Tile Groups

Prompt text stores longer reusable sentences.
Tile groups store reusable sets of tiles.

Use them for common character descriptions, styles, lighting setups, negative prompts, or workflow-specific prompt fragments.

---

## Center Pane

### Blocks and Tiles

The center pane contains the active prompt. Tiles can be arranged, enabled, disabled, and emphasized.

Each tile can represent:

- A tag.
- A longer prompt text item.
- A tile group item.
- Temporary text typed directly into the prompt.

### Tile Operations

Common operations:

- Drag tiles to reorder them.
- Turn a tile on or off.
- Edit text.
- Adjust emphasis or weight.
- Remove a tile from the current prompt.
- Save useful arrangements as a tile group.

In the block input field, comma-separated text such as `masterpiece, 1girl, blue eyes:1.2` creates multiple tag tiles. **Add Text** keeps the input as one natural text tile instead of splitting it.

Disabled tiles remain visible but are not sent to Invoke.

### Translation

PromptMosaic can use an optional local LLM server, such as LM Studio, for translation.

Translation can help keep local-language labels and Invoke prompt text aligned. Configure the translation LLM in settings before using this feature.

---

## Generation Bar

The generation bar controls the model and generation parameters.

### Model and Base

Choose the target model or base model before generating.

Generation requires a template for the selected base model. If no template exists, PromptMosaic disables generation and asks you to fetch a template from Invoke.

### Generation Parameters

Typical parameters include:

- Width / height
- Steps
- CFG scale
- Scheduler
- Positive / negative prompt
- Optional LoRAs

PromptMosaic patches known fields into the saved Invoke workflow graph. Unknown fields are left untouched, which helps templates survive many Invoke-side workflow changes.

### Seed Settings

Seed behavior depends on the current control:

- Random seed.
- Fixed seed.
- Reuse seed from a history item.
- Plan-specific seed behavior.

Use fixed seeds when comparing models or LoRA weights.

### Three Generation Buttons

The exact buttons depend on the selected mode and plan, but the generation controls are generally used for:

- Single generation.
- Plan or batch generation.
- Reusing / branching from an existing history state.

If a button is disabled, check the status message. Common causes are missing templates, an empty prompt, no selected model, or Invoke being disconnected.

---

## Right Pane

### Generation History

The history area stores generated images, prompt states, and generation parameters.

> Screenshot placeholder: right-pane history
> `docs/images/side_panel_history.png`

You can use history entries to:

- Restore a previous prompt.
- Compare results.
- Branch a new generation from an older image.
- Open the image viewer.
- Move entries into groups.

### History Review

History review tools help rename, inspect, and organize generated results.
Use them to keep useful generations easy to find.

### Group Management

Groups organize history entries. They are useful when comparing multiple attempts for one character, scene, model, or style.

### Notes

Notes can store text related to a prompt or generation workflow.

### Trash

Deleted items are moved to a trash area when supported. Review the trash before final cleanup if you need to recover an item.

---

## Generation Lineage

PromptMosaic records how generations branch from earlier states.

When you restore a history item and generate again, the new result becomes a child of that restored state instead of being only a flat list entry. This is useful when exploring variations.

---

## History Map

The history map visualizes generation lineage as a tree.

> Screenshot placeholder: full history map
> `docs/images/history_map_full.png`

### Operating Principle

Each node is a generation state. Edges show which generation came from which earlier state.

### View and Navigation

Use the history map to:

- Move around lineage visually.
- Click a node to reveal the matching history item in the right pane when the right pane is open.
- Right-click a node and choose **Open image window** to inspect the image.
- See branches at a glance.
- When the image window is already open, clicking nodes keeps the previous image-window-linked behavior.

### Organization

Use grouping, review, and deletion tools to keep long histories readable.

### Image Viewer

Right-click a node and choose **Open image window** to open a selected generated image for closer inspection.

### Color Customization

History colors and map positions can be customized or reset from settings.

---

## Multi-Model Plans

Multi-model plans let you generate through multiple model / LoRA / parameter combinations.

Typical uses:

- Compare several models with the same prompt.
- Compare LoRA weights.
- Run SDXL and other base-specific setups with matching templates.
- Keep repeatable test plans for a project.

> Screenshot placeholder: multi-model plan dialog
> `docs/images/plan_dialog.png`

Plans can also be managed from **Settings -> Generation Management -> Multi-Model Plan Management**.

---

## Generation Templates

A template is the actual Invoke txt2img workflow graph. PromptMosaic stores the graph and, at generation time, replaces only known fields such as prompt, seed, steps, CFG, scheduler, model, and size.

### Fetching Templates

- Use the **Invoke Data Acquisition** wizard. It opens automatically on first launch and can also be opened from settings.

### Behavior and Notes

- At least one template is required for each base model you want to generate with.
- Multiple templates can be registered for the same base model, and one can be marked as the default.
- When fetching a template for LoRA use, first create an Invoke txt2img generation that includes at least one LoRA. PromptMosaic reuses that LoRA route.
- VAE, refiner, and text encoder differences should be distinguished by template name.
- SDXL refiner stages stored inside the template are preserved. PromptMosaic mainly patches the base stage.
- Many minor Invoke workflow changes can be handled by fetching a fresh template.
- Template management supports duplicate, rename, set as default, delete, and template cache reset.

> Templates from another old Invoke environment are not guaranteed to work. Fetch templates from your current Invoke environment.

---

## Settings

Open settings with the gear button.

### Display

- **Theme** - Dark (Catppuccin Mocha) or Light (Catppuccin Latte). Applied after restart.
- **Font size** - Small, standard, large, or extra large.
- **Language** - 11 languages including Japanese and English.
- **Tile display** - local language only, prompt text only, or two-line display.
- **Tag input suggestions** - enable or disable suggestions in center-pane tag fields.
- **Uncategorized tile colors** - background, text, and border colors.
- **NSFW content** - show or hide NSFW-flagged models and LoRAs.
- **History colors / window positions** - reset history map and image viewer positions.
- **App icon** - choose a custom icon.

### Connection

- **Invoke URL** - default `http://localhost:9090`.
- **Queue ID** - default `default`.
- **Translation LLM / classification LLM** - URL, model, prompts, temperature, and seed for LM Studio or another compatible local LLM server.

### Generation Management

- Template management.
- Multi-model plan management.

### Templates

- List, duplicate, rename, set default, and delete registered templates.
- Fetch new templates from the **Invoke Data Acquisition** wizard.

### Data Management

- Backup guidance.
- Export / import tag data.
- Rebuild caches.

---

## Data Management

### Update

PromptMosaic user data is stored in the `data` folder. This includes fetched model information, generation templates, prompts, and history. During an update, keep this `data` folder and replace only the application files.

If you use the ZIP version:

1. Quit PromptMosaic.
2. For safety, copy the current `data` folder somewhere else.
3. Download and extract the new ZIP.
4. Copy the contents of the new folder into your existing PromptMosaic folder.
5. If Windows asks, choose to replace files with the same names.
6. In the existing PromptMosaic folder, double-click `update_windows.bat`.

`update_windows.bat` automatically copies the `data` folder to `_update_backups`, then updates the Python environment according to `requirements.txt`. Do not delete the old folder before copying the new files, or the `data` folder will be lost.

If you installed with Git:

```bat
git pull
update_windows.bat
```

### Backup

PromptMosaic backup is handled by copying the entire `data` folder.

- **Backup** - quit PromptMosaic, then copy the entire `data` folder to another location.
- **Restore** - quit PromptMosaic, then put the copied `data` folder back in place.
- `index.db` and `suggestions.db` are caches and will be rebuilt automatically on startup if missing.

> If you keep generated image files in another folder, copy that image folder separately when needed.

### Tag Data Export / Import

Tags and groups can be exported and imported as JSON / CSV.

- **Export** - write current tags and groups to JSON.
- **Add differences** - add only new tags and groups from a file.
- **Overwrite update** - add new items and overwrite existing items with the same names.

### Cache Management

- **Rebuild index** - scan the `data` folder and rebuild `index.db`.
- **Rebuild suggestions** - rebuild `suggestions.db` from libraries.
- `index.db` and `suggestions.db` are caches. Deleting or rebuilding them does not remove source data.

### Backup Basics

Copying database files while the app is running can create an incomplete backup. Quit PromptMosaic before backup or restore.

---

## Appendix

### External Inbox

The external inbox imports history candidates from other tools. If items exist, PromptMosaic asks whether to import them at launch or refresh. If cancelled, they remain in the inbox for later.

### PNG Metadata Import

Dropping a PNG into the app can import prompts from Invoke-style metadata such as `invokeai_metadata`, `invokeai`, or `sd-metadata`.

### Database Layout

Data is split across multiple SQLite databases under `data/`, including `app.db`, `environment.db`, `notes.db`, `history_*.db`, and `library_*.db`. `index.db` and `suggestions.db` are caches and can be rebuilt if missing.

### Startup / Troubleshooting

| Symptom | Fix |
| --- | --- |
| Virtual environment not found | Run `install_windows.bat` first |
| Qt DLL error | Avoid Conda / Anaconda Python and recreate `.venv` with python.org Python |
| Generate button is disabled | Fetch a template, select a model or plan, and make sure the prompt is not empty |
| Invoke disconnected | Start Invoke 6.13 or later and check Settings -> Connection URL / queue ID |
| Template fetch fails | In Invoke, generate one txt2img image using a LoRA, then fetch the template |

Status indicators: connected, disconnected, and checking.

---

## Related Documents

- [README](../README_EN.md)
- [Tutorial](TUTORIAL_EN.md)
- [Screenshot placeholders](images/README_EN.md)

