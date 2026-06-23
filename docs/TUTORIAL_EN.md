# PromptMosaic Tutorial - Your First Image

[Japanese](TUTORIAL.md) | [English](TUTORIAL_EN.md)

This tutorial walks first-time users through installing **PromptMosaic**, connecting it to InvokeAI, and generating the first image.
For detailed feature descriptions, see the [Manual](MANUAL_EN.md).

> Screenshot placeholder: full main window immediately after launch
> `docs/images/main_window.png`

---

## Table of Contents

1. [What PromptMosaic Is](#1-what-promptmosaic-is)
2. [Requirements](#2-requirements)
3. [Install and Launch](#3-install-and-launch)
4. [Connect to InvokeAI](#4-connect-to-invokeai)
5. [Main Window Layout](#5-main-window-layout)
6. [Build the First Prompt](#6-build-the-first-prompt)
7. [Choose Model and Parameters](#7-choose-model-and-parameters)
8. [Generate](#8-generate)
9. [Use History](#9-use-history)
10. [Next Steps](#10-next-steps)

---

## 1. What PromptMosaic Is

PromptMosaic is a companion GUI for InvokeAI.

- Manage prompts as reusable **tiles**.
- Organize tags, prompt text, and tile groups.
- Send generation jobs to InvokeAI.
- Review generation history and branch from past results.
- Run multiple models and LoRAs through multi-model plans.

PromptMosaic itself does not generate images. InvokeAI must be running and reachable from the local machine.

---

## 2. Requirements

- Windows 11
- Python 3.10-3.12 from python.org, or the Windows `py` launcher
- InvokeAI 6.13 or later
- A model that can generate images in InvokeAI

Conda / Anaconda Python is not required. If Conda is installed, use the included installer and launcher so PromptMosaic can use its own normal virtual environment without being affected by Conda DLL paths.

---

## 3. Install and Launch

Open PowerShell or Command Prompt in the PromptMosaic folder and run:

```bat
install_windows.bat
```

The installer creates `.venv` and installs the required packages.

> Screenshot placeholder: console after running `install_windows.bat`
> `docs/images/install_console.png`

Then launch:

```bat
PromptMosaic.bat
```

If the virtual environment is missing, run `install_windows.bat` first.

---

## 4. Connect to InvokeAI

Start InvokeAI 6.13 or later before running the first setup.

On first launch, PromptMosaic opens the **InvokeAI Data Acquisition** wizard.

> Screenshot placeholder: InvokeAI Data Acquisition wizard
> `docs/images/invoke_setup.png`

### Step 1: Fetch Models and LoRAs

Confirm the InvokeAI URL, normally:

```text
http://localhost:9090
```

Then fetch the model and LoRA lists. PromptMosaic stores the list locally so the generation UI can choose models and plans.

### Step 2: Fetch Generation Templates for Each Base Model

A generation template is the actual txt2img workflow graph saved from InvokeAI. PromptMosaic reuses the graph and only replaces known fields such as prompt, seed, steps, CFG, scheduler, model, and size.

For each base model you want to use:

1. In InvokeAI, generate one txt2img image with that base model.
2. If you plan to use LoRA, include at least one LoRA in that generation. PromptMosaic uses the LoRA path in the workflow as a reusable route.
3. In PromptMosaic, fetch and save the template from the wizard.

If the current base model has no template, generation is disabled until a template is available.

---

## 5. Main Window Layout

PromptMosaic uses a three-pane layout.

> Screenshot placeholder: annotated three-pane layout
> `docs/images/three_panes.png`

| Area | Purpose |
| --- | --- |
| Left pane | Browse tags, models, LoRAs, prompt text, and tile groups |
| Center pane | Build and edit the active prompt as tiles |
| Right pane | Review generation history, notes, groups, and deleted items |

The generation bar is located near the prompt editor and contains model, size, seed, and generation controls.

---

## 6. Build the First Prompt

Add a few tags or text tiles to the center prompt editor.

> Screenshot placeholder: center pane with arranged tiles
> `docs/images/tiles.png`

Typical workflow:

1. Search or select tags in the left pane.
2. Add them to the center pane.
3. Drag tiles to change order.
4. Toggle tiles on or off.
5. Adjust emphasis when needed.

Tiles are meant to make prompt editing repeatable. You can keep useful pieces as tags, prompt text, or tile groups and reuse them later.

---

## 7. Choose Model and Parameters

In the generation bar, choose:

- Model / base model
- Optional LoRA or multi-model plan
- Width and height
- Steps
- CFG scale
- Scheduler
- Seed mode

> Screenshot placeholder: generation bar
> `docs/images/generation_bar.png`

If a model cannot be selected or the generate button is disabled, check that the current base model has a saved generation template.

---

## 8. Generate

Use the generation buttons:

- **Generate** - send one generation job.
- **Generate All** - generate all configured targets in the current plan.
- **Enqueue / plan buttons** - use configured multi-model plan behavior when available.

PromptMosaic sends the job to InvokeAI. When the image is ready, it appears in the history area.

> Screenshot placeholder: first result visible in the right pane
> `docs/images/first_result.png`

---

## 9. Use History

The right pane keeps generated results so you can compare them, reopen prompts, and branch from previous outputs.

The history map shows lineage as a tree.

> Screenshot placeholder: history map lineage tree
> `docs/images/history_map.png`

Common actions:

- Select a past generation to restore its prompt and parameters.
- Branch from a result by generating again.
- Review or rename history entries.
- Open the image viewer from a history item.

---

## 10. Next Steps

After the first successful generation, try:

- Registering frequently used prompts as tile groups.
- Creating a multi-model plan.
- Fetching templates for additional base models.
- Enabling LM Studio integration for translation or automatic classification.
- Creating encrypted backups with `.pmbak`.

For complete details, continue with the [Manual](MANUAL_EN.md).
