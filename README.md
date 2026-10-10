# DrainWatch

AI-powered urban drainage monitoring system. DrainWatch ingests drone and CCTV camera frames, runs transformer-based semantic segmentation to differentiate water flow from solid waste occlusion, computes a per-drain Choke Index, and automatically surfaces critical blockages to a real-time municipal operations dashboard. When the Choke Index for a drain crosses 70%, the system generates a prioritised dispatch queue so field crews can be routed before a choke-point overflows.

This repository contains the full production stack: the Python inference backend, the trained SegFormer checkpoint, the React operations dashboard, and the AWS infrastructure configuration used to run it on ECS Fargate.

---

## Table of Contents

- [Problem Statement](#problem-statement)
- [System Architecture](#system-architecture)
- [Technology Stack](#technology-stack)
- [AWS Infrastructure](#aws-infrastructure)
- [Machine Learning Pipeline](#machine-learning-pipeline)
- [Backend API](#backend-api)
- [Frontend Dashboard](#frontend-dashboard)
- [Repository Structure](#repository-structure)
- [Local Development](#local-development)
- [Deployment](#deployment)
- [Environment Variables](#environment-variables)
- [API Reference](#api-reference)
- [Credentials and Access](#credentials-and-access)

---

## Problem Statement

Urban municipalities manage thousands of open-drain networks that are monitored entirely reactively. Crews are dispatched only after a drain overflows and damages surrounding zones. There is no automated telemetry capable of detecting solid waste accumulation before a blockage becomes catastrophic.

DrainWatch addresses this by treating every camera feed as a sensor. Each uploaded frame is passed through a segmentation model that labels every pixel as either clear water channel, debris occlusion, or surrounding surface. The resulting pixel ratios are converted into a Choke Index score and persisted to a database, giving operations teams a quantitative, searchable record of drain health over time.

---

## System Architecture

```
Camera Feed (Drone / CCTV)
        |
        v
  [FastAPI Backend]
        |
        +-- SegFormer Segmentation Model (PyTorch, CPU)
        |        |
        |        +-- Pixel-level water vs. debris classification
        |        +-- Choke Index computation (0-100%)
        |        +-- Segmentation overlay image generation
        |
        +-- Storage Router
        |        |
        |        +-- Amazon S3  (source image + overlay image)
        |        +-- Amazon DynamoDB  (analysis record, choke score, timestamp)
        |
        +-- Auth Router
        |        |
        |        +-- Supabase  (user accounts, roles, session cookies)
        |
        +-- REST API  (served to React dashboard)
        |
  [React Dashboard]  (served as SPA from same FastAPI container)
        |
        +-- Overview        (fleet-level choke index summary)
        +-- Inspections     (per-drain analysis history)
        +-- Incidents       (auto-generated critical alerts above 70% threshold)
        +-- Dispatch        (prioritised crew assignment queue)
        +-- Operational Map (geospatial drain status overlay)
        +-- Administration  (user and source management)
```

The entire backend and frontend are packaged into a single Docker image and run as one container on AWS ECS Fargate. The frontend is compiled to a static bundle at build time and served directly by the FastAPI process via `StaticFiles`. There is no separate frontend server.

---

## Technology Stack

**Backend**

| Component | Technology |
|-----------|-----------|
| API framework | FastAPI 0.115+ |
| ML model | SegFormer-b0 (HuggingFace Transformers) |
| Deep learning | PyTorch 2.6 (CPU) |
| Image processing | Pillow |
| Segmentation scoring | Custom Choke Index algorithm |
| Authentication | Supabase (PostgreSQL-backed) |
| Package management | uv / pyproject.toml |
| Python version | 3.12 |

**Frontend**

| Component | Technology |
|-----------|-----------|
| Framework | React 18 + TypeScript |
| Build tool | Vite |
| Styling | Vanilla CSS (design tokens, no framework) |
| Mapping | Leaflet |
| Icons | Lucide React |
| Fonts | Inter (Google Fonts) |

**Infrastructure**

| Component | Service |
|-----------|---------|
| Container runtime | AWS ECS Fargate |
| Container registry | Amazon ECR |
| Image storage | Amazon S3 |
| Telemetry database | Amazon DynamoDB |
| User management | Supabase (Auth) |
| Logging | Amazon CloudWatch Logs |
| IAM | Custom task execution and task roles |

---

## AWS Infrastructure

DrainWatch is deployed to the `ap-southeast-2` (Sydney) region. The full infrastructure consists of:

**Amazon ECR**

The Docker image is stored in a private ECR repository (`drainwatch`). The image is approximately 1.5 GB and is built for the `linux/amd64` platform to run on ECS Fargate.

**Amazon ECS Fargate**

The container runs as a Fargate task with 1 vCPU and 3 GB of memory. ECS manages availability, restarts, and rolling deploys. The service maintains one running task at all times.

**Amazon S3**

All visual output from the ML pipeline is stored in the `drainwatch-media-prod` bucket. For each analysis, two objects are written: the original source frame uploaded by the operator, and the AI-generated segmentation overlay. Objects are publicly readable so the dashboard can render them directly.

**Amazon DynamoDB**

The `drainwatch-readings` table stores one record per analysis. The partition key is `drain_id` and the sort key is `timestamp`, which allows efficient time-series queries per drain. The table uses on-demand billing so there are no capacity planning requirements.

**IAM Roles**

Two IAM roles are used:

- `ecsTaskExecutionRole` allows ECS to pull the image from ECR and write logs to CloudWatch.
- `drainwatchTaskRole` is the role assumed by the running container. It has scoped permissions to read and write S3 objects under the `drainwatch-*` prefix and to query and write DynamoDB tables under the `drainwatch*` prefix.

**Security Group**

Port 8080 is open to `0.0.0.0/0` so the dashboard is reachable over the public internet. The Fargate task receives a public IP address directly, without a load balancer.

**CloudWatch Logs**

All container stdout and stderr is streamed to the `/ecs/drainwatch` log group.

---

## Machine Learning Pipeline

**Model**

DrainWatch uses [SegFormer-b0](https://huggingface.co/nvidia/segformer-b0-finetuned-ade-512-512), a transformer-based semantic segmentation model pre-trained on ADE20K. The ADE20K pre-training is intentional: the encoder already represents water, road, earth, and vegetation, which covers the visual vocabulary of urban drainage environments. The classification head is fine-tuned on a composite dataset built from:

- ADE20K water and channel classes
- COCO segmentation masks for debris-related categories
- Custom drain images from the Delhi pilot area (DTU main gate, Bawana Industrial, Narela)

**Choke Index**

After segmentation, each pixel is classified into one of three semantic groups:

1. Clear water channel (passable flow)
2. Solid waste or debris (occlusion)
3. Background surface (drain walls, road)

The Choke Index is computed as the ratio of debris pixels to total drain channel pixels (debris + clear water). A score of 0 means the drain is fully clear. A score of 100 means the visible channel is completely occluded.

Three alert bands are defined:

| Band | Threshold | Meaning |
|------|-----------|---------|
| Clear | Below 40% | No action required |
| Watch | 40% to 70% | Schedule inspection |
| Critical | Above 70% | Dispatch crew immediately |

**Segmentation Overlay**

For each analysis, a colour-coded overlay image is generated and stored alongside the source frame. Blue pixels represent identified water channel. Red pixels represent debris occlusion. This overlay is displayed in the Inspections page of the dashboard.

**CPU Compatibility**

The model runs on CPU in the ECS container. A compatibility patch in `segformer.py` handles a known issue in PyTorch 2.6 where `torch.accelerator.current_accelerator()` raises a `RuntimeError` in CPU-only environments when called by the Transformers library at import time.

---

## Backend API

The FastAPI application exposes the following route groups:

**Analysis** (`/api/analyze`)

Accepts a multipart image upload and an optional drain ID. Runs the segmentation model, computes the Choke Index, generates the overlay, writes both images to S3, and persists the record to DynamoDB. Returns the Choke Index, risk band, image URLs, and class-level pixel breakdown.

**Operations** (`/api/operations`)

Returns aggregated drain health data for the dashboard. Includes all readings, per-drain history, and grouped summaries.

**Auth** (`/api/auth`)

Login, logout, current user, and user management endpoints. Sessions are maintained via HTTP-only cookies. Passwords are stored and validated through Supabase.

**Admin** (`/api/admin`)

User creation, role assignment, and monitoring source registration. Restricted to users with the ADMIN role.

**Health** (`/health`)

Returns a JSON object indicating whether the model loaded successfully, which device it is running on, and which storage backend is active. Used by ECS health checks.

---

## Frontend Dashboard

The dashboard is a single-page application served from the same container as the API. After the Vite build, the compiled output is placed at `frontend/dist/` and mounted at the root path by FastAPI.

**Pages**

- **Landing** - Public marketing page describing the system. Accessible without login.
- **Login** - Authentication form. On success, a session cookie is set.
- **Overview** - Fleet-level summary. Shows total drains monitored, number in each alert band, and a recent activity feed.
- **Inspections** - Per-drain inspection list. Each inspection shows the source frame, the segmentation overlay, the Choke Index score, the alert band, and the timestamp.
- **Incidents** - All drains currently above the critical threshold. Sorted by Choke Index descending.
- **Dispatch** - Crew assignment interface. Operators can assign field crews to incidents and mark work orders as in progress or resolved.
- **Operational Map** - Leaflet-based map with drain asset markers colour-coded by alert band.
- **Administration** - User management and monitoring source registration. Admin-only.
- **Profile** - User account settings including timezone and notification preferences.
- **History** - Full time-series history for a selected drain.

---

## Repository Structure

```
drainwatch/
|
+-- src/drainwatch/              Python package (backend)
|   +-- api/
|   |   +-- main.py              FastAPI application, lifespan, static file mount
|   |   +-- storage.py           Storage abstraction (S3 + DynamoDB or local)
|   |   +-- scoring.py           Choke Index computation and band classification
|   |   +-- schemas.py           Pydantic request and response models
|   |   +-- routers/
|   |       +-- analysis.py      Image upload and inference endpoint
|   |       +-- auth.py          Login, logout, session management
|   |       +-- operations.py    Dashboard data endpoints
|   |       +-- admin.py         User and source management
|   +-- model/
|   |   +-- segformer.py         SegFormer loading, inference, CPU compatibility patch
|   |   +-- train.py             Fine-tuning script
|   |   +-- evaluate.py          Evaluation metrics
|   |   +-- metrics.py           IoU and accuracy helpers
|   +-- choke/
|   |   +-- index.py             Choke Index algorithm
|   |   +-- overlay.py           Segmentation overlay image generation
|   +-- data/                    Dataset loaders and preprocessing
|   +-- ops/                     Domain models, serialisation, security helpers
|   +-- config.py                Centralised constants and path definitions
|   +-- cli.py                   Command-line tools for training and seeding
|
+-- frontend/                    React + TypeScript (dashboard)
|   +-- src/
|   |   +-- pages/               One file per dashboard page
|   |   +-- components/          AppShell, Sidebar, Map, AnalyzeFrame, UI primitives
|   |   +-- api/                 Typed fetch wrappers for each backend route group
|   |   +-- lib/format.ts        Date, time, and Choke Index formatting utilities
|   +-- index.html
|   +-- vite.config.ts
|   +-- package.json
|
+-- docker/
|   +-- Dockerfile               Multi-stage build: Node (frontend) then Python (runtime)
|   +-- ecs-task-definition.json ECS Fargate task definition (1 vCPU, 3 GB)
|   +-- deploy.sh                One-command build, push, and redeploy script
|
+-- checkpoints/
|   +-- best/                    Trained SegFormer checkpoint (model.safetensors + config.json)
|
+-- data/
|   +-- local_store/             Local filesystem fallback for images and readings
|
+-- pyproject.toml               Python project metadata and dependencies
+-- .env.example                 Template for required environment variables
```

---

## Local Development

**Prerequisites**

- Python 3.12
- Node.js 20
- uv (Python package manager) or pip

**Backend**

```bash
# Create and activate virtual environment
python -m venv .venv
source .venv/bin/activate

# Install the package and its dependencies
pip install -e .

# Copy the environment variable template and fill in your values
cp .env.example .env

# Start the API server
PYTHONPATH=src uvicorn drainwatch.api.main:app --host 0.0.0.0 --port 8000 --reload
```

The API will be available at `http://localhost:8000`. The health endpoint at `/health` confirms whether the model and storage backends loaded correctly.

**Frontend**

```bash
cd frontend
npm install
npm run dev
```

The development server runs at `http://localhost:5173` and proxies API requests to the backend at port 8000.

**Seeding Local Data**

The CLI includes a seed command that writes synthetic readings with segmentation images to the local store, so the dashboard has data to display without running real analyses:

```bash
PYTHONPATH=src python -m drainwatch.cli seed
```

---

## Deployment

The `docker/deploy.sh` script handles the full build and deploy cycle. It logs into ECR, builds the Docker image, pushes it, registers a new task definition revision, and forces a rolling redeploy of the ECS service.

```bash
bash docker/deploy.sh
```

To deploy manually:

```bash
REGION="ap-southeast-2"
ACCOUNT_ID="328996809086"
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/drainwatch"

# Authenticate to ECR
aws ecr get-login-password --region $REGION \
  | docker login --username AWS --password-stdin \
    "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"

# Build for linux/amd64 (required for Fargate)
docker build --platform linux/amd64 -f docker/Dockerfile -t ${ECR_URI}:latest .

# Push
docker push ${ECR_URI}:latest

# Force rolling deploy
aws ecs update-service \
  --cluster drainwatch-cluster \
  --service drainwatch-service \
  --task-definition drainwatch \
  --force-new-deployment \
  --region $REGION
```

**Stopping the service to save cost**

When not in active use, scale the service to zero tasks. You pay nothing for Fargate compute while the service is at zero. S3 and DynamoDB continue to persist all data.

```bash
# Stop (free)
aws ecs update-service --cluster drainwatch-cluster \
  --service drainwatch-service --desired-count 0 --region ap-southeast-2

# Resume
aws ecs update-service --cluster drainwatch-cluster \
  --service drainwatch-service --desired-count 1 --region ap-southeast-2
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SUPABASE_URL` | Yes | Supabase project URL for authentication |
| `SUPABASE_KEY` | Yes | Supabase service role key |
| `DRAINWATCH_BUCKET` | No | S3 bucket name for image storage. If unset, images are stored locally under `data/local_store/` |
| `DRAINWATCH_TABLE` | No | DynamoDB table name for readings. If unset, readings are stored in a local JSON file |
| `DRAINWATCH_MEDIA_BASE` | No | Base URL for publicly accessible S3 images (e.g. `https://bucket.s3.region.amazonaws.com`) |
| `AWS_REGION` | No | AWS region for S3 and DynamoDB clients. Defaults to `ap-south-1` |
| `DRAINWATCH_CHECKPOINT` | No | Path to the trained SegFormer checkpoint directory. Defaults to `checkpoints/best/` |
| `DRAINWATCH_DEVICE` | No | PyTorch device string (`cpu` or `cuda`). Auto-detected if not set |
| `PORT` | No | Port the server listens on. Defaults to `8080` in the container |

The storage backend selection is automatic. If `DRAINWATCH_BUCKET` and `DRAINWATCH_TABLE` are both set, AWS storage is used. Otherwise the system falls back to the local filesystem, which is suitable for development and testing without any cloud credentials.

---

## API Reference

**POST /api/analyze**

Accepts a multipart form with one image file and an optional `drain_id` string. Returns:

```json
{
  "drain_id": "D-1042",
  "choke_index": 0.784,
  "band": "critical",
  "class_breakdown": {
    "water": 0.12,
    "debris": 0.78,
    "background": 0.10
  },
  "source_url": "https://drainwatch-media-prod.s3.ap-southeast-2.amazonaws.com/...",
  "overlay_url": "https://drainwatch-media-prod.s3.ap-southeast-2.amazonaws.com/...",
  "timestamp": "2026-10-10T06:00:00Z"
}
```

**POST /api/auth/login**

```json
{ "email": "operator@drainwatch.in", "password": "operator123" }
```

Sets an HTTP-only session cookie (`dw_session`) on success. Returns the user object.

**GET /health**

```json
{
  "status": "ok",
  "model_loaded": true,
  "device": "cpu",
  "storage": "aws",
  "thresholds": {
    "watch": 0.4,
    "critical": 0.7,
    "min_channel_frac": 0.005,
    "min_channel_px_floor": 512,
    "min_confidence": 0.55
  }
}
```

---

## Credentials and Access

The system ships with two pre-seeded accounts. These are stored in Supabase and work against both the locally running API and the AWS deployment.

| Role | Email | Password | Access |
|------|-------|----------|--------|
| Admin | `admin@drainwatch.in` | `admin123` | Full access including Administration page |
| Operator | `operator@drainwatch.in` | `operator123` | Inspections, Dispatch, Incidents, Map |

The ADMIN role grants access to user creation, role assignment, and monitoring source registration.
