# Nigh.ty Dashboard

Imagine being night and want to go to a cozy place....yeah, this is that. Have fun!

## Getting started

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the API and dashboard:

```bash
uvicorn app.main:app --reload
```

The HTML dashboard is available at `http://localhost:8000/dashboard` and the JSON API is namespaced under `/api`.

## Environment variables

The application reads configuration from environment variables with the `DASHBOARD_` prefix.

| Variable | Description | Default |
| --- | --- | --- |
| `DASHBOARD_DATABASE_URL` | Database connection string used by SQLAlchemy/Alembic. | `sqlite:///./app.db` |
| `DASHBOARD_JWT_SECRET_KEY` | Secret key used to sign JWT access tokens and server-side sessions. | `change-me` |
| `DASHBOARD_JWT_ALGORITHM` | JWT signing algorithm. | `HS256` |
| `DASHBOARD_ACCESS_TOKEN_EXPIRE_MINUTES` | Expiration time for access tokens in minutes. | `1440` |

## Database migrations

Alembic is configured under the `alembic/` directory. To create the database schema using the configured environment variable run:

```bash
alembic upgrade head
```

## Testing

Run the automated test suite with:

```bash
pytest
```

The tests cover authentication flows and statistics endpoints to ensure tokens, permissions, and stat aggregation work as expected.
