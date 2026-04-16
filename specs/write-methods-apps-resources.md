# CLI Write-методы для apps/resources (манифест + узкие команды)

**Статус:** реализовано (итерация 2). Последнее обновление: 2026-04-15.

## Context

Нужно реализовать в CLI write-операции для apps и resources. Сложности:
1. Приложение содержит несколько ресурсов разных типов (web-app / worker / cron-job), каждый с нетривиальной структурой.
2. Ресурсы могут деплоиться из GitHub, но у пользователя может не быть подключения на момент вызова CLI.

Решения:
- **Стиль**: YAML-манифест + узкие императивные команды (полный набор операций).
- **GitHub**: require pre-connected. CLI отдаёт ошибку со ссылкой на веб-UI, если установки нет.
- **Identity app-а**: helm-style позиционный аргумент `novps apps apply <app-name> -f file.yaml`. Значение `<app-name>` — это `Application.name`, уникальный на уровне проекта через partial unique index `(project_id, name) WHERE deleted_at IS NULL`.
- **Identity ресурса**: `Resource.name` в рамках app (lookup через `ResourcesCrud.find_by_application_and_name`).
- **Prune**: удалять ресурсы, отсутствующие в манифесте, по флагу `--prune` (CLI-side diff + DELETE).
- **Secrets**: подстановка `${VAR}` из `os.environ` + опциональный `.env` файл (через `--env-file`). Shell env перекрывает значения из `.env`.
- **Scope манифеста**: один app на файл; без databases/s3. Верхний уровень: `{resources: [...], envs: [...]}`. `resource` соответствует бэкендовому `ResourceType`.

## Ключевое решение

Для public-api flow **не переиспользуется** `PlanDeserializer` (он identity-based по `Alias`). Вместо этого — inline-оркестратор внутри endpoint'а `PUT /public-api/apps/{app_name}/apply`, который использует существующие `CreateApp`/`CreateAppResource`/`UpdateAppResource`/`StoreEnvironmentVariables`/`CreateNewDeployment` и делает lookup по `Application.name`. Внутренний `PUT /projects/{project_id}/deployment-plan/` и alias-based flow остались нетронутыми.

## Реализация

### Backend — модель и миграция
- [x] Partial unique index `idx_application_project_id_name_unique` на `Application (project_id, name) WHERE deleted_at IS NULL` — `backend/app/models.py:302`.
- [x] Alembic-миграция `b7c8d9e0f1a2` — `backend/migrations/versions/20260415_1447-b7c8d9e0f1a2.py`.

### Backend — use-case и CRUD
- [x] `CreateApp.execute(project, alias=None, name=None)` — опциональный `name` подменяет `UniqueIdGenerator` (`backend/app/use_case/create_app.py:20`). Обратная совместимость сохранена.
- [x] `ApplicationsCrud.find_by_name(project, name, with_deleted=False)` — `backend/app/crud/applications.py:33`.
- [x] `ResourcesCrud.find_by_application_and_name(application, name, with_deleted=False)` — `backend/app/crud/resources.py:213`.
- [x] `PublicApiApplyAppRequest` в `backend/app/schema.py:391`.

### Backend — public-api endpoints (PAT-auth)
- [x] `GET /public-api/github/installations` (PROJECT_READ) — `backend/app/routing/public_api/github.py`. Возвращает `[{id, account_name, profile_picture}]` для проекта.
- [x] `PATCH /public-api/apps/{app_id}` (APPS_UPDATE) — `backend/app/routing/public_api/apps.py`. Обновляет name/description/envs_global; при смене name проверяет уникальность в проекте (409). Activity logs: `environment_variables.updated`, `updated` с diff, per-resource `deployment.created` при env-update.
- [x] `DELETE /public-api/apps/{app_id}` (APPS_DELETE) — soft-delete + `delete_application` job + activity (`project.app_deleted`, `deleted`).
- [x] `POST /public-api/apps/{app_id}/deployment` (APPS_DEPLOY) — force redeploy всех ресурсов + activity (`deployment.created` per-resource, `redeployed`).
- [x] `PUT /public-api/apps/{app_name}/apply` — основной endpoint для `apply`:
  - Валидирует уникальность resource names в payload.
  - Lookup app по `find_by_name`; при отсутствии создаёт через `CreateApp.execute(project, None, name=app_name)`.
  - Pre-compute `total_spend` для новых не-cron ресурсов; `usage_based_charger.charge` с idempotency key (`app:{name}:new` или `app:{id}:apply:{ts}`); 402 при нехватке баланса.
  - Per-resource: create-or-update через `CreateAppResource`/`UpdateAppResource`; store resource envs для созданных.
  - При создании app: `create_namespace` job + `project.app_created` + `created`.
  - Per created resource: `ResourceEventLogger.log("created", ...)` для биллинга, `create_internal_domain` для web-app, deploy-resource-lock (10 min Redis TTL), activity `resource.created` + `deployment.created`.
  - Per updated resource: activity `resource.updated` + `deployment.created` с changes.
  - Финально: `CreateNewDeployment.execute` (trigger `manual`, reason `CLI apply`) + `process_deployment` task.
  - Возвращает `{app: {id, name, created}, deployment_id, resources: [{name, id, action}]}`.
- [x] `GET /public-api/apps/{app_id}/deployments/{deployment_id}` (APPS_READ) — статус конкретного деплоя, для опроса из CLI при `--wait`.
- [x] `GET /public-api/apps/{app_name}/export?include_secrets=` (APPS_READ; `include_secrets=true` дополнительно требует `APPS_SHOW_SECRETS`) — возвращает payload в форме `PublicApiApplyAppRequest` (envs + resources с полным source/config/replicas/envs/volumes). При `include_secrets=false` env-значения возвращаются пустыми, ключи сохраняются для round-trip.
- [x] `PATCH /public-api/resources/{resource_id}` (RESOURCES_UPDATE) — `backend/app/routing/public_api/resources.py`. Использует `UpdateAppResource`; создаёт deployment (если changed и не `do_not_deploy`); activity `resource.updated` + `deployment.created` с changes.
- [x] `DELETE /public-api/resources/{resource_id}` (RESOURCES_DELETE) — `ResourceEventLogger.log("deleted")` + soft-delete + deployment + `delete_internal_domain` для web-app + activity `resource.deleted` / `deleted`.
- [x] `POST /public-api/resources/{resource_id}/deployment` (APPS_DEPLOY) — ручной деплой ресурса + activity `deployment.created` / `resource.deployment_created`.
- [x] `GET /public-api/resources/{resource_id}/deployments/{deployment_id}` (APPS_READ) — статус деплоя ресурса.
- [x] Регистрация роутеров в `backend/app/routing/public_api/__init__.py`.

### Backend — RBAC
- [x] Используются существующие permissions: `APPS_READ/CREATE/UPDATE/DELETE/DEPLOY`, `RESOURCES_UPDATE/DELETE`, `PROJECT_READ`. Новых permission-ов не требуется.

### CLI — клиент и манифест
- [x] `NoVPSClient.put(path, data)` добавлен в `cli/src/novps/client.py:28`.
- [x] `cli/src/novps/manifest.py` — YAML loader с `${VAR}` substitution из `os.environ` и опционального `.env` файла (shell env перекрывает `.env`). Dependency `PyYAML` в `cli/pyproject.toml`.

### CLI — команды
- [x] `novps github list` — `cli/src/novps/commands/github.py`, зарегистрирован в `cli/src/novps/main.py`.
- [x] `novps apps apply <app-name> -f FILE [--env-file FILE] [--prune] [--dry-run] [--wait] [--json]` — `cli/src/novps/commands/apps.py`:
  - Парсит YAML через `manifest.load_manifest`; `--env-file` даёт дополнительный source переменных (shell env перекрывает).
  - Pre-flight GitHub через `GET /github/installations`, если в манифесте есть `source_type: github`.
  - Шлёт `PUT /apps/{app_name}/apply`.
  - При `--prune` сравнивает имена ресурсов в ответе против манифеста и удаляет лишние через `DELETE /resources/{id}`.
  - При `--wait` опрашивает `GET /apps/{app_id}/deployments/{deployment_id}` до terminal status (`success`/`failed`/`canceled`), poll interval 3s, timeout 20 мин.
- [x] `novps apps export <app-name> [-o FILE] [--include-secrets]` — дампит YAML-манифест текущего app (совместимый с `apply`). По умолчанию значения env'ов не выводятся (`value: ""`), `--include-secrets` требует `apps.show-secrets`.
- [x] `novps apps update <app_id> [--name] [--description]` — PATCH.
- [x] `novps apps delete <app_id> [--force]` — с confirmation "DELETE".
- [x] `novps apps deploy <app_id>` — force redeploy.
- [x] `novps resources update <resource_id> [--image] [--tag] [--replicas SIZE:COUNT] [--command] [--port] [--schedule] [--env KEY=VAL] [--no-deploy]` — `cli/src/novps/commands/resources.py`.
- [x] `novps resources scale <resource_id> --replicas SIZE:COUNT` — shortcut для update.
- [x] `novps resources set-image <resource_id> [--image] [--tag]` — shortcut.
- [x] `novps resources set-env <resource_id> KEY=VAL [KEY=VAL ...] [--merge|--replace]` — merge с существующими envs (по умолчанию) или replace.
- [x] `novps resources delete <resource_id> [--force]` — с confirmation.
- [x] `novps resources deploy <resource_id>` — ручной деплой.

## Пример манифеста

```yaml
# novps.yaml
envs:
  - key: LOG_LEVEL
    value: info
resources:
  - name: api
    type: web-app
    source_type: github
    source:
      type: github
      repository: owner/repo
      branch: main
      source_dir: ./backend
      build_command: docker build .
      build_envs:
        - key: NODE_ENV
          value: production
    config:
      command: ""
      port: "8080"
      restart_policy: always
    replicas:
      type: sm
      count: 2
    envs:
      - key: DB_URL
        value: ${DB_URL}            # подставится из os.environ
    volumes: []
```

Команда: `novps apps apply my-api -f novps.yaml --wait`.

## Переиспользованные компоненты

- Use-cases: `CreateApp` (с новым параметром `name`), `CreateAppResource`, `UpdateAppResource`, `CreateNewDeployment`, `StoreEnvironmentVariables`.
- Services: `ResourceEventLogger`, `UsageBasedCharger`, `Queue`, `Github`.
- Schemas: `ResourceType`, `UpdateAppRequest`, `UpdateResourceRequest`, `ResourceEnvsType`.
- CLI helpers: `get_client`, `output`, `print_json`, `console` из `novps.client`/`novps.output`.

## Верификация

Статус: синтаксис всех изменённых файлов проходит `ast.parse`; CLI-команды корректно регистрируются; манифест-парсер протестирован локально (`${VAR}` подставляются, resource names извлекаются).

Для полной боевой верификации необходимо:
1. **Backend**: применить миграцию `b7c8d9e0f1a2`, запустить `python -m uvicorn main:app --reload`.
2. **CLI**: прогнать сценарии:
   - `novps github list` — пустой список для проекта без GitHub.
   - `novps apps apply my-docker -f fixtures/docker-only.yaml` — создание app+resource без GitHub.
   - `novps apps apply my-api -f fixtures/github-source.yaml` — ожидаемо падает с понятной ошибкой без GitHub; успешно после подключения через веб-UI.
   - Повторный `apply` — update (name совпадает), изменения только в diff-полях.
   - `apply --prune` после удаления ресурса из YAML — ресурс удаляется.
   - `novps resources scale <id> --replicas lg:3` — обновляет replicas.
   - `novps apps delete <id> --force` — soft-delete.
   - `novps apps apply <name> --wait` — блокируется до завершения деплоя.
3. **Integration**: проверить в веб-UI, что созданные через CLI app/resource видны корректно (statuses, deployments, logs, activity log).

## Критические файлы

### Backend
- `backend/app/models.py:302` — partial unique index на Application.
- `backend/migrations/versions/20260415_1447-b7c8d9e0f1a2.py` — миграция.
- `backend/app/use_case/create_app.py:20` — `CreateApp.execute(..., name=None)`.
- `backend/app/crud/applications.py:33` — `find_by_name`.
- `backend/app/crud/resources.py:213` — `find_by_application_and_name`.
- `backend/app/schema.py:391` — `PublicApiApplyAppRequest`.
- `backend/app/routing/public_api/apps.py` — PATCH/DELETE/POST /deployment/PUT apply/GET export endpoints.
- `backend/app/routing/public_api/resources.py` — PATCH/DELETE/POST /deployment endpoints.
- `backend/app/routing/public_api/github.py` — GET /github/installations.
- `backend/app/routing/public_api/__init__.py` — регистрация роутеров.

### CLI
- `cli/pyproject.toml` — +PyYAML.
- `cli/src/novps/client.py:28` — `put` метод.
- `cli/src/novps/manifest.py` — YAML loader + env substitution + `.env` файл.
- `cli/src/novps/main.py` — регистрация `github` sub-app.
- `cli/src/novps/commands/apps.py` — apply/update/delete/deploy/export.
- `cli/src/novps/commands/resources.py` — update/scale/set-image/set-env/delete/deploy.
- `cli/src/novps/commands/github.py` — list.

## Отложено на следующие итерации

- `novps apps create --name ...` / `novps resources create ...` как узкие императивные команды (сейчас эквивалент доступен через `apply` + манифест).
- Интерактивный `novps github connect` (OAuth flow из CLI). Сейчас — только веб-UI.
- Поддержка нескольких apps в одном манифесте.
- Поддержка databases/s3 buckets в манифесте.
