# Audit Log & Activity Feed — Implementation Reference

## Current Implementation

### Files Created
| File | Purpose |
|---|---|
| `audit/views.py` | Activity feed view with filtering, pagination, staff/user scoping |
| `audit/urls.py` | URL routing for `/accounts/audit-log/` |
| `audit/templatetags/audit_tags.py` | `audit_target_name` template tag for DB lookup fallback |
| `audit/management/commands/cleanup_audit_log.py` | Retention cleanup (default 90 days) |
| `audit/management/commands/backfill_audit_names.py` | Backfill target names for old entries |
| `templates/audit/audit_log.html` | Activity feed page (card-based layout) |
| `templates/audit/_log_description.html` | Human-readable descriptions per action type |

### URL
`/accounts/audit-log/` (staff sees all, users see own)

### View Features
- Filter by action type (dropdown)
- Filter by date range
- 50 entries per page with pagination
- Staff sees all users' activity, regular users see only their own

### Template Design
- Card-based layout (not table)
- Action badges with color coding (10 distinct colors)
- Human-readable descriptions via `_log_description.html` partial
- Key name display for API key events (via `audit_target_name` tag)
- Full plaintext key shown for `apikey.created` events
- User displayed as `by user #ID` (not email)
- Width: `max-w-[95%]` (matches fleet overview)

### Audit Events
| Action | App | Target | Metadata |
|---|---|---|---|
| `user.registered` | accounts | User | email, is_admin |
| `user.session.login` | accounts | User | ip |
| `user.session.logout` | accounts | User | — |
| `apikey.created` | accounts | ApiKey | name, key_prefix, plaintext |
| `apikey.revoked` | accounts | ApiKey | name |
| `apikey.reactivated` | accounts | ApiKey | name |
| `apikey.deleted` | accounts | ApiKey | name |
| `apikey.transferred` | accounts | ApiKey | name, from_user, to_user, from_user_email, to_user_email, rig_count |
| `tag.created` | accounts | RigTag | name, color |
| `tag.updated` | accounts | RigTag | name, color |
| `tag.deleted` | accounts | RigTag | name |
| `tag.added` | dashboard | Rig | tag, rig_name |
| `tag.removed` | dashboard | Rig | tag, rig_name |
| `rig.enrolled` | metrics_app | Rig | agent_version |
| `rig.renamed` | dashboard | Rig | old_name, new_name |
| `rig.deleted` | dashboard | Rig | name |

### Data Retention
- `python manage.py cleanup_audit_log --days=90` — deletes entries older than 90 days
- Integrated into `data_retention.sh` as Phase 4

### Backfill Command
- `python manage.py backfill_audit_names` — looks up target names from DB for old entries missing metadata
- Run once to fix historical data

### Navigation
- Desktop: "Activity" link in nav bar
- Mobile: "Activity" link in hamburger menu dropdown
