"""CLI entry point for the Enterprise Tenant RAG production server."""
from __future__ import annotations

import argparse
import os


def main():
    ap = argparse.ArgumentParser(description="tenant-rag Enterprise Production Server")
    ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8080)))
    ap.add_argument("--data-dir", default=os.environ.get("TENANT_RAG_DATA_DIR", "./enterprise_data"))
    ap.add_argument("--admin-token", default=os.environ.get("TENANT_RAG_ADMIN_TOKEN"))
    ap.add_argument("--demo-mode", action="store_true", help="Enable interactive demo bootstrap & fixed demo admin token")
    ap.add_argument("--seed-demo", action="store_true", help="Seed Aurora Cloud & Cedar MedTech demo corpora on startup")
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()

    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "Enterprise server dependencies missing. Install with: pip install -e '.[enterprise]'"
        ) from exc

    from .app import create_app

    app = create_app(
        data_dir=args.data_dir,
        admin_token=args.admin_token,
        demo_mode=args.demo_mode,
        seed_demo=args.seed_demo,
    )
    print(
        f"tenant-rag-enterprise serving on http://{args.host}:{args.port} "
        f"(data_dir={args.data_dir}, demo_mode={args.demo_mode})"
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
