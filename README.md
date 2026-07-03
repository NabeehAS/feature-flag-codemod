# 🧹 Feature Flag Codemod
> **Project Codename:** AI-Powered Feature Flag Undertaker

> **Status:** 🚧 Active Development (V1 – CLI + Deterministic LibCST Engine)

An autonomous platform for deterministically removing deprecated feature flags from Python codebases.

Removing stale feature flags is a repetitive and error-prone maintenance task. Rather than allowing an LLM to directly edit source code, this project uses AI only for discovery and planning. Every code modification is performed by a deterministic **LibCST** transformation engine that preserves formatting and produces reviewable source code changes.

---

## ✨ Features

- Deterministic source-to-source code transformations using **LibCST**
- Formatting-preserving code mutations (no regex-based rewriting)
- AI-assisted feature flag discovery through structured execution plans
- Automated validation with **pytest**
- Sandboxed execution using **Docker** *(planned)*
- Automated GitHub Pull Request generation *(planned)*

---

## 🪄 Example Transformation

**Target flag:** `ENABLE_STRIPE_V2 = True`

### Before

```python
def process_payment():
    # Initialize the gateway
    print("Starting payment...")

    if ENABLE_STRIPE_V2:
        print("Using Stripe V2 API")
        return True
    else:
        print("Using Legacy API")
        return False
```

### After

```python
def process_payment():
    # Initialize the gateway
    print("Starting payment...")

    print("Using Stripe V2 API")
    return True
```

---

## 🧠 Architecture

The project deliberately separates AI reasoning from deterministic code execution.
AI produces structured plans only; the transformation engine remains the sole authority for modifying source code.

```text
Repository
    │
    ▼
AI Discovery
(Structured Execution Plan)
    │
    ▼
Deterministic LibCST Transformation Engine
    │
    ▼
Validation (pytest / Docker)
    │
    ▼
GitHub Pull Request
```

### 1. Discovery

An AI layer identifies deprecated feature flags and generates a structured execution plan.

### 2. Transformation

LibCST executes deterministic, formatting-preserving code transformations.

### 3. Validation

Mutated code is validated automatically using **pytest** and, in later versions, an isolated Docker sandbox.

### 4. Delivery

Validated changes can be submitted automatically as GitHub Pull Requests.

---

## 🛠️ Tech Stack

### Current

- Python 3.12+
- LibCST
- argparse
- pytest

### Planned

- Docker SDK for Python
- PyGithub
- OpenAI API
- Pydantic (Structured Outputs)

---

## 🗺️ Roadmap

- [x] **V0** – Deterministic LibCST transformation engine
- [x] **V1** – CLI for single-file transformations
- [x] **V2** – Repository scanning
- [x] **V3** – Scope analysis & import cleanup
- [x] **V4** – Automated pytest validation
- [ ] **V5** – Docker sandbox
- [ ] **V6** – GitHub Pull Request automation
- [ ] **V7** – AI-assisted feature flag discovery
- [ ] **V8** – Production pipeline orchestration

---

## 🚀 Current Usage

```bash
python -m project.cli.main \
    --file path/to/target.py \
    --flag ENABLE_NEW_UI \
    --state false
```

---

## 🎯 Learning Objectives

This project is designed to demonstrate practical software engineering skills in:

- Compiler tooling with **LibCST**
- Static analysis and source-to-source transformations
- AI orchestration with structured outputs
- Automated testing and validation
- Secure sandboxed execution
- GitOps automation
- Production-oriented Python application architecture
