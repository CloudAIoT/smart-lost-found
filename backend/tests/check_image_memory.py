"""Linux container probe: real preprocessing/parser/SDK load, synthetic model response.

Run with --memory 320m --memory-swap 320m --network none; feed this file to python stdin.
This does not measure network transport, real inference latency, or recognition quality.
"""
import gc
from io import BytesIO
import json
from pathlib import Path
import resource
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import numpy as np
from PIL import Image
from app.main import app  # Include the application's resident memory.
from app.config import Settings
from app.image_analysis import GeminiFeatureAnalyzer
from app.integrations import AnalysisInput
from imageprocess.app.services.gemini_service import GeminiService

settings = Settings(_env_file=None, postgres_db='test', postgres_user='test', postgres_password='test',
                    gemini_api_key='test-only-key')
features = dict.fromkeys(('category', 'color', 'shape', 'surface_texture', 'brand', 'visible_text',
                          'structure', 'distinctive_features'))
features['color'] = ['black']
original_load = GeminiService.load


def load(self):
    original_load(self)
    self.client.models.generate_content = lambda **kwargs: SimpleNamespace(
        text=json.dumps(features), candidates=[SimpleNamespace(finish_reason='STOP')], usage_metadata=None)


for size in ((2304, 1296), (2000, 2000)):
    shots = []
    for _ in range(2):
        noise = np.random.default_rng(1).integers(0, 256, (*reversed(size), 3), dtype=np.uint8)
        photo = Image.fromarray(noise)
        buffer = BytesIO()
        photo.save(buffer, format='JPEG', quality=95)
        shots.append(AnalysisInput(uuid4(), buffer.getvalue()))
        photo.close()
        buffer.close()
        del noise, photo
    gc.collect()
    with patch.object(GeminiService, 'load', load):
        for _ in range(5):
            analyzer = GeminiFeatureAnalyzer(settings)
            with analyzer.slot():
                result = analyzer.analyze(uuid4(), tuple(shots))
            assert result.features['color'] == ['black']
    print(json.dumps({'size': size, 'repeats': 5,
        'rss_peak_mib': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 2),
        'cgroup_peak_mib': round(int(Path('/sys/fs/cgroup/memory.peak').read_text()) / 1048576, 2),
        'memory_events': dict(line.split() for line in Path('/sys/fs/cgroup/memory.events').read_text().splitlines())}))
