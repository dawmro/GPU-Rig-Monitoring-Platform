# Docker Manifest Extension Implementation Plan

## Overview
Extend the Docker container manifest collection in the agent to include additional useful fields from `docker inspect`, store them in the database, and display them in the Container Details section of the Live Metrics page.

## Current State Analysis

### Current Manifest Fields Collected (agent/run.py - collect_docker_inspect):
- `image_tag` - from Config.Image
- `digest` - from ImageManifestDescriptor
- `size` - image size
- `media_type` - OCI media type
- `platform` - architecture/OS
- `annotations` - OCI/Docker annotations
- `image_tag` - from Config.Image
- `labels` - container labels
- `state` - started_at, exit_code, oom_killed, error
- `mounts` - volumes with source, destination, type, rw
- `networks` - IP, gateway, MAC per network

### Database Model (LatestDockerContainer)
- `manifest_json` - JSONField storing all manifest data
- `logs_json` - log lines

### Template Display (Container Details section)
Currently shows: Image, Digest, Platform, State, Mounts, Networks, Labels, Annotations

---

## 1. Agent Implementation (agent/run.py + agent_windows/run.py)

### Version Bump
- `__version__`: `1.8.2` → `1.9.0` (minor version bump for new data)
- `__schema_version__`: `1.13` → `1.14` (new payload fields)

### Additional Fields to Extract from docker inspect

Based on the docker inspect output analysis, add these fields to the manifest:

```python
# In collect_docker_inspect() function, after line ~848 (after networks):

# Port Bindings (HostConfig.PortBindings)
host_config = data.get('HostConfig', {})
if host_config.get('PortBindings'):
    manifest['port_bindings'] = host_config['PortBindings']

# Resource Limits
resource_limits = {}
for key in ['CpuShares', 'Memory', 'MemorySwap', 'NanoCpus', 
            'CpuQuota', 'CpuPeriod', 'CpuPeriod', 'BlkioWeight']:
    if host_config.get(key) is not None:
        resource_limits[key.lower()] = host_config[key]
if resource_limits:
    manifest['resource_limits'] = resource_limits

# Restart Policy
restart_policy = host_config.get('RestartPolicy', {})
if restart_policy:
    manifest['restart_policy'] = {
        'name': restart_policy.get('Name'),
        'max_retry': restart_policy.get('MaximumRetryCount')
    }

# Resource Reservations
reservations = {}
for key in ['MemoryReservation', 'KernelMemory', 'CpuCount', 'CpuPercent']:
    if host_config.get(key) is not None:
        reservations[key.lower()] = host_config[key]
if reservations:
    manifest['resource_reservations'] = reservations

# Restart Count (top-level)
if data.get('RestartCount') is not None:
    manifest['restart_count'] = data['RestartCount']

# Created Time
if data.get('Created'):
    manifest['created'] = data['Created']

# Exposed Ports
config = data.get('Config', {})
if config.get('ExposedPorts'):
    manifest['exposed_ports'] = list(config['ExposedPorts'].keys())

# Working Directory
if config.get('WorkingDir'):
    manifest['working_dir'] = config['WorkingDir']

# Entrypoint / Cmd
if config.get('Entrypoint'):
    manifest['entrypoint'] = config['Entrypoint']
if config.get('Cmd'):
    manifest['cmd'] = config['Cmd']

# User
if config.get('User'):
    manifest['user'] = config['User']

# Health Check
if config.get('Healthcheck'):
    manifest['healthcheck'] = config['Healthcheck']

# Security
security = {}
for key in ['CapAdd', 'CapDrop', 'SecurityOpt', 'Privileged', 'ReadonlyRootfs']:
    if host_config.get(key) is not None:
        security[key.lower()] = host_config[key]
if security:
    manifest['security'] = security

# DNS
dns = {}
for key in ['Dns', 'DnsOptions', 'DnsSearch', 'ExtraHosts']:
    if host_config.get(key) is not None:
        dns[key.lower()] = host_config[key]
if dns:
    manifest['dns'] = dns

# Restart Policy (already have)
# Restart Count (top-level)
# Restart Policy Name + Max Retry

# Restart Count (top-level)
if data.get('RestartCount') is not None:
    manifest['restart_count'] = data['RestartCount']

# Created Time
if data.get('Created'):
    manifest['created'] = data['Created']

# Exposed Ports
config = data.get('Config', {})
if config.get('ExposedPorts'):
    manifest['exposed_ports'] = list(config['ExposedPorts'].keys())

# Working Directory
if config.get('WorkingDir'):
    manifest['working_dir'] = config['WorkingDir']

# Entrypoint / Cmd
if config.get('Entrypoint'):
    manifest['entrypoint'] = config['Entrypoint']
if config.get('Cmd'):
    manifest['cmd'] = config['Cmd']

# User
if config.get('User'):
    manifest['user'] = config['User']

# Health Check
if config.get('Healthcheck'):
    manifest['healthcheck'] = config['Healthcheck']

# Security
security = {}
for key in ['CapAdd', 'CapDrop', 'SecurityOpt', 'Privileged', 'ReadonlyRootfs']:
    if host_config.get(key) is not None:
        security[key.lower()] = host_config[key]
if security:
    manifest['security'] = security

# DNS
dns = {}
for key in ['Dns', 'DnsOptions', 'DnsSearch', 'ExtraHosts']:
    if host_config.get(key) is not None:
        dns[key.lower()] = host_config[key]
if dns:
    manifest['dns'] = dns
```

**Version Bump:**
- `__version__ = '1.9.0'` (minor - new data fields)
- `__schema_version__ = '1.14'` (new payload structure)

---

## 2. Windows Agent (agent_windows/run.py)

Apply identical changes to `collect_docker_inspect()` function in `agent_windows/run.py`:
- Same version bump: `__version__ = '1.9.0-win'`, `__schema_version__ = '1.14'`
- Add identical field extraction logic

---

## 2. Serializer & Database Model Updates

### Database Model (gpu_monitor/metrics_app/models.py)

**LatestDockerContainer model** - Add explicit fields for commonly queried data (optional, since manifest_json is JSONField):

```python
class LatestDockerContainer(models.Model):
    # ... existing fields ...
    
    # New optional fields for common queries (stored alongside manifest_json)
    port_bindings_json = models.JSONField(default=dict, blank=True)
    resource_limits_json = models.JSONField(default=dict, blank=True)
    restart_policy_json = models.JSONField(default=dict, blank=True)
    restart_count = models.PositiveIntegerField(null=True, blank=True)
    exposed_ports_json = models.JSONField(default=list, blank=True)
    working_dir = models.CharField(max_length=255, blank=True, default='')
    entrypoint_json = models.JSONField(default=list, blank=True)
    cmd_json = models.JSONField(default=list, blank=True)
    user = models.CharField(max_length=255, blank=True, default='')
    healthcheck_json = models.JSONField(default=dict, blank=True)
    security_json = models.JSONField(default=dict, blank=True)
    dns_json = models.JSONField(default=dict, blank=True)
    restart_count = models.PositiveIntegerField(null=True, blank=True)
    created = models.CharField(max_length=64, blank=True, default='')
    exposed_ports_json = models.JSONField(default=list, blank=True)
    working_dir = models.CharField(max_length=255, blank=True, default='')
    entrypoint_json = models.JSONField(default=list, blank=True)
    cmd_json = models.JSONField(default=list, blank=True)
    user = models.CharField(max_length=255, blank=True, default='')
    healthcheck_json = models.JSONField(default=dict, blank=True)
    security_json = models.JSONField(default=dict, blank=True)
    dns_json = models.JSONField(default=dict, blank=True)
    restart_count = models.PositiveIntegerField(null=True, blank=True)
    created = models.CharField(max_length=64, blank=True, default='')
```

**Alternative (simpler):** Keep using `manifest_json` JSONField only, since it's already a JSONField and can store all new fields. Just update the serializer to populate additional keys in `manifest_json`. No model changes needed!

**Decision:** Keep using `manifest_json` JSONField only. No model changes needed.

### Serializer (gpu_monitor/metrics_app/serializers.py)

In `process_ingest()`, the `LatestDockerContainer.objects.create()` already stores `manifest_json=container.get('manifest', {})`. No changes needed - new fields automatically stored in JSONField.

**However**, we should update the serializer to extract and store the new fields in `ls_defaults` for LatestSnapshot if we want them in the denormalized snapshot:

```python
# In process_ingest(), add to ls_defaults:
'container_port_bindings_json': docker_containers[0].get('manifest', {}).get('port_bindings', {}) if docker_containers else {},
# ... but this is per-container, not per-rig
# Better: store in LatestDockerContainer.manifest_json only
```

**Decision:** No serializer changes needed. All new fields go into `manifest_json` JSONField automatically.

---

## 3. Template Updates (Container Details Section)

**File:** `gpu_monitor/templates/dashboard/_metrics_cards.html`

Add new sections to the Container Details manifest display:

```html
<!-- Add after Annotations section (around line 743) -->

{% if c.manifest.port_bindings %}
<div class="flex gap-2">
    <dt class="text-gray-500 w-20 flex-shrink-0">Port Bindings:</dt>
    <dd class="text-gray-300">
        {% for host_port, bindings in c.manifest.port_bindings.items %}
        <div class="truncate">{{ host_port }} → {% for b in bindings %}{{ b.HostIp }}:{{ b.HostPort }}{% if not forloop.last %}, {% endif %}{% endfor %}</div>
        {% endfor %}
    </dd>
</div>
{% endif %}

{% if c.manifest.resource_limits %}
<div class="flex gap-2">
    <dt class="text-gray-500 w-20 flex-shrink-0">Resources:</dt>
    <dd class="text-gray-300">
        {% for k, v in c.manifest.resource_limits.items %}
        <div class="truncate">{{ k }}: {{ v }}</div>
        {% endfor %}
    </dd>
</div>
{% endif %}

{% if c.manifest.restart_policy %}
<div class="flex gap-2">
    <dt class="text-gray-500 w-20 flex-shrink-0">Restart:</dt>
    <dd class="text-gray-300">
        {{ c.manifest.restart_policy.name }}{% if c.manifest.restart_policy.max_retry > 0 %} (max {{ c.manifest.restart_policy.max_retry }}){% endif %}
    </dd>
</div>
{% endif %}

{% if c.manifest.resource_reservations %}
<div class="flex gap-2">
    <dt class="text-gray-500 w-20 flex-shrink-0">Reservations:</dt>
    <dd class="text-gray-300">
        {% for k, v in c.manifest.resource_reservations.items %}
        <div class="truncate">{{ k }}: {{ v }}</div>
        {% endfor %}
    </dd>
</div>
{% endif %}

{% if c.manifest.restart_count is not None %}
<div class="flex gap-2">
    <dt class="text-gray-500 w-20 flex-shrink-0">Restarts:</dt>
    <dd class="text-gray-300">{{ c.manifest.restart_count }}</dd>
</div>
{% endif %}

{% if c.manifest.created %}
<div class="flex gap-2">
    <dt class="text-gray-500 w-20 flex-shrink-0">Created:</dt>
    <dd class="text-gray-300">{{ c.manifest.created }}</dd>
</div>
{% endif %}

{% if c.manifest.exposed_ports %}
<div class="flex gap-2">
    <dt class="text-gray-500 w-20 flex-shrink-0">Exposed:</dt>
    <dd class="text-gray-300">
        {% for port in c.manifest.exposed_ports %}
        <span class="text-gray-300 mr-1">{{ port }}</span>
        {% endfor %}
    </dd>
</div>
{% endif %}

{% if c.manifest.working_dir %}
<div class="flex gap-2">
    <dt class="text-gray-500 w-20 flex-shrink-0">Workdir:</dt>
    <dd class="text-gray-300 truncate">{{ c.manifest.working_dir }}</dd>
</div>
{% endif %}

{% if c.manifest.entrypoint %}
<div class="flex gap-2">
    <dt class="text-gray-500 w-20 flex-shrink-0">Entrypoint:</dt>
    <dd class="text-gray-300 truncate">{{ c.manifest.entrypoint|join:" " }}</dd>
</div>
{% endif %}

{% if c.manifest.cmd %}
<div class="flex gap-2">
    <dt class="text-gray-500 w-20 flex-shrink-0">Cmd:</dt>
    <dd class="text-gray-300 truncate">{{ c.manifest.cmd|join:" " }}</dd>
</div>
{% endif %}

{% if c.manifest.user %}
<div class="flex gap-2">
    <dt class="text-gray-500 w-20 flex-shrink-0">User:</dt>
    <dd class="text-gray-300">{{ c.manifest.user }}</dd>
</div>
{% endif %}

{% if c.manifest.healthcheck %}
<div class="flex gap-2">
    <dt class="text-gray-500 w-20 flex-shrink-0">Healthcheck:</dt>
    <dd class="text-gray-300">{{ c.manifest.healthcheck.Test|join:" " }}</dd>
</div>
{% endif %}

{% if c.manifest.security %}
<div class="flex gap-2">
    <dt class="text-gray-500 w-20 flex-shrink-0">Security:</dt>
    <dd class="text-gray-300">
        {% for k, v in c.manifest.security.items %}
        <div class="truncate">{{ k }}: {{ v }}</div>
        {% endfor %}
    </dd>
</div>
{% endif %}

{% if c.manifest.dns %}
<div class="flex gap-2">
    <dt class="text-gray-500 w-20 flex-shrink-0">DNS:</dt>
    <dd class="text-gray-300">
        {% for k, v in c.manifest.dns.items %}
        <div class="truncate">{{ k }}: {{ v }}</div>
        {% endfor %}
    </dd>
</div>
{% endif %}
```

---

## 4. Missing Crucial Steps Checklist

### Must-Do Steps:
- [ ] Update agent version: `__version__ = '1.9.0'`, `__schema_version__ = '1.14'` (both agents)
- [ ] Add all new field extraction in `collect_docker_inspect()` (both agents)
- [ ] Verify serializer handles new fields (automatic via JSONField)
- [ ] Add template sections for new manifest fields
- [ ] Test with various container configurations

### Optional Enhancements:
- [ ] Add pagination for log lines (100 lines may be long)
- [ ] Add search/filter for log lines
- [ ] Add "Copy to clipboard" for digest/command
- [ ] Add tooltips for technical fields

### Database Migration:
- **Not required** - using JSONField for all new fields
- If adding explicit model fields: `python manage.py makemigrations metrics_app`

### Testing Checklist:
- [ ] Container with port bindings
- [ ] Container with resource limits
- [ ] Container with health check
- [ ] Container with restart policy
- [ ] Container with mounts, networks, labels
- [ ] Container with no manifest data
- [ ] Empty logs handling
- [ ] Non-json-file log driver

---

## Migration Strategy

1. **Deploy agents first** (v1.9.0 / 1.9.0-win) - backward compatible
2. **Deploy server** - handles new fields automatically
3. **Verify** in UI - new fields appear in Container Details

---

## Files to Modify

1. `agent/run.py` - `collect_docker_inspect()` + version bump
2. `agent_windows/run.py` - same changes + version bump
3. `gpu_monitor/templates/dashboard/_metrics_cards.html` - template additions
3. (Optional) `gpu_monitor/metrics_app/models.py` - if adding explicit fields

---

## Notes

- All new fields are optional (use `.get()` with defaults)
- Backward compatible - older agents won't send new fields
- JSONField handles arbitrary new keys automatically
- No database migration needed for JSONField approach
- Version bump signals new data availability to UI
