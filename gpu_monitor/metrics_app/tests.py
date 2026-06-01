import json
import uuid
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from accounts.models import ApiKey
from rigs.models import Rig
from metrics_app.models import MetricSnapshot, LatestSnapshot

User = get_user_model()


class IngestAPITestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com', password='testpass123', username='testuser'
        )
        self.api_key = ApiKey.objects.create(
            user=self.user,
            name='test-key',
            key_hash=ApiKey.hash_key('test-api-key-123'),
        )
        self.rig_uuid = str(uuid.uuid4())
        self.client = Client()

    def _make_payload(self, rig_uuid=None, **overrides):
        payload = {
            'rig_uuid': rig_uuid or self.rig_uuid,
            'schema_version': '1.0',
            'agent_version': '1.0.0',
            'timestamp': '2024-05-20T14:32:00Z',
            'inventory': {'cpu': {'model': 'Test CPU'}},
            'metrics': {
                'cpu': {'utilization_pct': 45.2, 'temp_c': 65.0},
                'memory': {'total_bytes': 16000000000, 'used_bytes': 8000000000},
                'gpus': [{'model': 'RTX 4090', 'gpu_util_pct': 80, 'temp_c': 70}],
            },
            'software': {'hostname': 'test-rig', 'kernel': '5.15.0'},
            'errors': [],
        }
        payload.update(overrides)
        return payload

    def test_ingest_creates_rig_and_snapshot(self):
        """First ingest should create rig and return 200."""
        payload = self._make_payload()
        response = self.client.post(
            '/api/v1/ingest/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_API_KEY='test-api-key-123',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'new')

        # Rig should be created
        rig = Rig.objects.get(uuid=self.rig_uuid)
        self.assertEqual(rig.owner, self.user)
        self.assertEqual(rig.status, 'Online')

        # Snapshot should exist
        snapshot = LatestSnapshot.objects.get(rig_uuid=self.rig_uuid)
        self.assertEqual(snapshot.cpu_utilization_pct, 45.2)

    def test_ingest_idempotent(self):
        """Duplicate ingest should return 202."""
        payload = self._make_payload()
        # First request
        self.client.post(
            '/api/v1/ingest/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_API_KEY='test-api-key-123',
        )
        # Duplicate
        response = self.client.post(
            '/api/v1/ingest/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_API_KEY='test-api-key-123',
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()['status'], 'duplicate')

    def test_ingest_invalid_api_key(self):
        """Invalid API key should return 401."""
        payload = self._make_payload()
        response = self.client.post(
            '/api/v1/ingest/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_API_KEY='wrong-key',
        )
        self.assertEqual(response.status_code, 401)

    def test_ingest_missing_rig_uuid(self):
        """Missing rig_uuid should return 400."""
        payload = self._make_payload()
        del payload['rig_uuid']
        response = self.client.post(
            '/api/v1/ingest/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_API_KEY='test-api-key-123',
        )
        self.assertEqual(response.status_code, 400)

    def test_ingest_uuid_claimed_by_other_user(self):
        """UUID claimed by another user should return 409."""
        other_user = User.objects.create_user(
            email='other@example.com', password='testpass123', username='otheruser'
        )
        Rig.objects.create(uuid=self.rig_uuid, owner=other_user)

        payload = self._make_payload()
        response = self.client.post(
            '/api/v1/ingest/',
            data=json.dumps(payload),
            content_type='application/json',
            HTTP_X_API_KEY='test-api-key-123',
        )
        self.assertEqual(response.status_code, 409)

    def test_health_endpoint(self):
        """Health endpoint should return 200."""
        response = self.client.get('/api/v1/health/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['status'], 'healthy')


class RigModelTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com', password='testpass123', username='testuser'
        )

    def test_rig_creation(self):
        rig = Rig.objects.create(
            name='Test Rig',
            owner=self.user,
            expected_gpus=4,
        )
        self.assertEqual(rig.status, 'Offline')
        self.assertIsNone(rig.last_seen)

    def test_rig_str(self):
        rig = Rig.objects.create(name='My Rig', owner=self.user)
        self.assertIn('My Rig', str(rig))


class ApiKeyTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='test@example.com', password='testpass123', username='testuser'
        )

    def test_key_hashing(self):
        plaintext = 'my-secret-key'
        key_hash = ApiKey.hash_key(plaintext)
        self.assertNotEqual(key_hash, plaintext)
        self.assertTrue(key_hash.startswith('$argon2'))

    def test_key_validation(self):
        plaintext = 'test-key-456'
        key = ApiKey.objects.create(
            user=self.user,
            name='test',
            key_hash=ApiKey.hash_key(plaintext),
        )
        key_obj, error = ApiKey.validate_key(plaintext)
        self.assertIsNotNone(key_obj)
        self.assertIsNone(error)

    def test_invalid_key_validation(self):
        key_obj, error = ApiKey.validate_key('nonexistent-key')
        self.assertIsNone(key_obj)
