# Backend Service

`backend/` is the FastAPI service layer for the New Energy Sys prototype.

The package owns HTTP/API orchestration, authentication, report loading, and
task triggering.  Core data processing, modeling, inference, and dispatch logic
remain in `src/new_energy_sys/` so CLI workflows keep their stable imports.

## Development Startup

```powershell
$env:PYTHONPATH="src;."
python -m uvicorn backend.app.main:app --reload --host 127.0.0.1 --port 8000
```

The Vue frontend still calls same-origin `/api` paths.  In local development,
Vite proxies those requests to `http://localhost:8000`.

## Production Environment Variables

```powershell
$env:NES_APP_ENV="production"
$env:NES_JWT_SECRET="replace-with-a-strong-secret"
$env:NES_CORS_ORIGINS="https://your-frontend-domain.example"
$env:NES_USERS_JSON='{"admin":{"password_hash":"<sha256>","role":"admin"}}'
```

Production mode refuses the default demo secret, demo users, and wildcard CORS.

## Compatibility

`src/new_energy_sys/api/` is kept as a short-term compatibility shim.  New
commands, documentation, and tests should use:

```powershell
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000
```

Pitfall: when running from source on Windows, include both `src` and the project
root in `PYTHONPATH`; otherwise either `new_energy_sys` or `backend` imports can
fail.
