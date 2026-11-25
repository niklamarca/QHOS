"""Loopback-only XTTS voice service. It has no Trader access or browser surface."""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import os
import re
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path


MAX_TEXT_CHARS = 1800
SAMPLE_RATE = 24_000
MIN_INTERNAL_SILENCE_S = 0.7
MAX_INTERNAL_SILENCE_S = 0.3
SILENCE_THRESHOLD = 0.01


def speech_text(text: str) -> str:
    """Remove display markup and URLs, keeping the words intended for speech."""
    text = re.sub(r'```[\s\S]*?```', ' Trecho de código disponível no chat. ', str(text or ''))
    text = re.sub(r'\[([^\]]+)\]\(https?://[^\s)]+\)', r'\1', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'(?m)^\s*(?:[-*#>]\s+|\d+[.)]\s+)', '', text)
    return ' '.join(re.sub(r'[*_`#]', '', text).split())


def speech_segments(text: str, limit: int = 180) -> list[str]:
    """Bound every generation well below XTTS's 400-token context limit."""
    sentences = re.split(r'(?<=[.!?;])\s+', text)
    chunks = []
    for sentence in sentences:
        remaining = sentence.strip()
        while len(remaining) > limit:
            cut = remaining.rfind(' ', 0, limit + 1)
            if cut <= 0:
                cut = limit
            chunks.append(remaining[:cut].strip())
            remaining = remaining[cut:].strip()
        if remaining:
            if chunks and len(chunks[-1]) < 40 and len(chunks[-1]) + len(remaining) + 1 <= limit:
                chunks[-1] += ' ' + remaining
            else:
                chunks.append(remaining)
    return chunks


def loopback_host(value: str) -> str:
    if str(value or "").strip().lower() not in {"127.0.0.1", "localhost"}:
        raise ValueError("voice service permits only loopback binding")
    return "127.0.0.1"


def shorten_long_silences(waveform, sample_rate: int):
    """Keep natural gaps but remove model-generated dead air from a response."""
    import numpy as np

    values = np.asarray(waveform, dtype=np.float32).reshape(-1)
    quiet = np.abs(values) < SILENCE_THRESHOLD
    minimum = int(sample_rate * MIN_INTERNAL_SILENCE_S)
    retained = int(sample_rate * MAX_INTERNAL_SILENCE_S)
    parts: list = []
    cursor = 0
    index = 0
    while index < len(values):
        if not quiet[index]:
            index += 1
            continue
        start = index
        while index < len(values) and quiet[index]:
            index += 1
        # Do not clip leading/trailing silence, or normal conversational gaps.
        if start and index < len(values) and index - start >= minimum:
            parts.extend((values[cursor:start], values[start:start + retained]))
            cursor = index
    parts.append(values[cursor:])
    return np.concatenate(parts) if len(parts) > 1 else values


@dataclass(frozen=True)
class VoiceSettings:
    model_dir: Path
    reference_wav: Path
    device: str = "cuda"
    speed: float = 0.98
    temperature: float = 0.45
    repetition_penalty: float = 5.0
    reference_wavs: tuple[Path, ...] = ()

    @property
    def references(self) -> tuple[Path, ...]:
        return self.reference_wavs or (self.reference_wav,)

    @classmethod
    def from_environment(cls) -> "VoiceSettings":
        from qhos2.config import Settings, validar_data_root
        model_dir = validar_data_root(os.environ.get("BRAIN_VOICE_MODEL_DIR",
                                    str(Settings.from_environ().data_root / "brain" / "voice" / "models" / "xtts-v2")))
        reference_wav = Path(os.environ.get("BRAIN_VOICE_REFERENCE_WAV", str(model_dir / "samples/pt_sample.wav"))).resolve()
        references = json.loads(os.environ.get('BRAIN_VOICE_REFERENCE_WAVS', '[]'))
        if not isinstance(references, list) or any(not isinstance(p, str) or not p.strip() for p in references):
            raise ValueError('voice_references_must_be_path_array')
        return cls(
            model_dir=model_dir,
            reference_wav=reference_wav,
            device=os.environ.get("BRAIN_VOICE_DEVICE", "cuda").strip().lower() or "cuda",
            speed=float(os.environ.get("BRAIN_VOICE_SPEED", "0.98")),
            temperature=float(os.environ.get("BRAIN_VOICE_TEMPERATURE", "0.45")),
            repetition_penalty=float(os.environ.get("BRAIN_VOICE_REPETITION_PENALTY", "5.0")),
            reference_wavs=tuple(Path(p).resolve() for p in references),
        )

    def validate(self) -> None:
        required = ("config.json", "model.pth", "vocab.json", "dvae.pth", "mel_stats.pth")
        if not self.model_dir.is_dir() or any(not (self.model_dir / name).is_file() for name in required):
            raise RuntimeError("xtts_model_files_missing")
        if not 1 <= len(self.references) <= 4 or any(not p.is_file() or p.suffix.lower() not in {".wav", ".flac", ".mp3", ".m4a", ".ogg"} for p in self.references):
            raise RuntimeError("voice_reference_missing")
        if not 0.7 <= self.speed <= 1.3:
            raise RuntimeError("voice_speed_out_of_range")
        if not 0.1 <= self.temperature <= 1.0:
            raise RuntimeError("voice_temperature_out_of_range")
        if not 1.0 <= self.repetition_penalty <= 10.0:
            raise RuntimeError("voice_repetition_penalty_out_of_range")


class XttsVoice:
    def __init__(self, settings: VoiceSettings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._model = None
        self._torch = None
        self._conditioning = None
        self._device = "unloaded"

    @property
    def ready(self) -> bool:
        return self._model is not None and self._conditioning is not None

    def load(self) -> None:
        with self._lock:
            if self.ready:
                return
            self.settings.validate()
            import torch
            import numpy as np
            import soundfile as sf
            from scipy.signal import resample_poly
            from TTS.tts.configs.xtts_config import XttsConfig
            import TTS.tts.models.xtts as xtts_module
            from TTS.tts.models.xtts import Xtts

            def load_reference_audio(audio_path, sampling_rate):
                """Avoid torchcodec; its CUDA 13 binary is unavailable on this host."""
                samples, source_rate = sf.read(audio_path, dtype="float32", always_2d=True)
                mono = np.mean(samples, axis=1, dtype=np.float32)
                if source_rate != sampling_rate:
                    from math import gcd
                    divisor = gcd(int(source_rate), int(sampling_rate))
                    mono = resample_poly(mono, sampling_rate // divisor, source_rate // divisor).astype(np.float32)
                return torch.from_numpy(np.clip(mono, -1.0, 1.0)).unsqueeze(0)

            xtts_module.load_audio = load_reference_audio

            config = XttsConfig()
            config.load_json(str(self.settings.model_dir / "config.json"))
            model = Xtts.init_from_config(config)
            model.load_checkpoint(config, checkpoint_dir=str(self.settings.model_dir), eval=True)
            use_cuda = self.settings.device == "cuda" and torch.cuda.is_available()
            if use_cuda:
                model.cuda()
                torch.set_float32_matmul_precision("high")
            model.eval()
            conditioning = model.get_conditioning_latents(
                audio_path=[str(path) for path in self.settings.references],
                max_ref_length=6, gpt_cond_len=6 * len(self.settings.references),
                gpt_cond_chunk_len=6, sound_norm_refs=True,
            )
            self._model = model
            self._torch = torch
            self._conditioning = conditioning
            self._device = "cuda" if use_cuda else "cpu"

    def synthesize(self, text: str) -> bytes:
        if len(str(text or '')) > MAX_TEXT_CHARS:
            raise ValueError('invalid_text')
        clean = speech_text(text)
        if not re.search(r'\w', clean):
            raise ValueError("invalid_text")
        self.load()
        with self._lock, self._torch.inference_mode():
            import numpy as np
            gpt_cond_latent, speaker_embedding = self._conditioning
            waveforms = []
            for segment in speech_segments(clean):
                if not re.search(r'\w', segment):
                    continue
                result = self._model.inference(
                    # Some Portuguese XTTS references verbalize a terminal dot as
                    # "ponto". Sentence boundaries already have an explicit pause.
                    segment.rstrip('.'), 'pt', gpt_cond_latent, speaker_embedding,
                    temperature=self.settings.temperature,
                    repetition_penalty=self.settings.repetition_penalty,
                    top_k=50, top_p=0.86, do_sample=False,
                    speed=self.settings.speed, enable_text_splitting=False,
                )
                waveform = shorten_long_silences(result['wav'], SAMPLE_RATE)
                # Smooth only the edges, avoiding clicks at sentence boundaries.
                fade = min(int(SAMPLE_RATE * .008), len(waveform) // 2)
                if fade:
                    waveform = waveform.copy()
                    waveform[:fade] *= np.linspace(0, 1, fade)
                    waveform[-fade:] *= np.linspace(1, 0, fade)
                if waveforms:
                    waveforms.append(np.zeros(int(SAMPLE_RATE * .16), dtype=np.float32))
                waveforms.append(waveform)
            audio = np.concatenate(waveforms)
        import soundfile as sf

        target = io.BytesIO()
        sf.write(target, audio, SAMPLE_RATE, format="WAV", subtype="PCM_16")
        return target.getvalue()

    def snapshot(self) -> dict:
        return {
            "ready": self.ready,
            "model": "coqui/XTTS-v2",
            "language": "pt",
            "sample_rate": SAMPLE_RATE,
            "device": self._device,
            "speed": self.settings.speed,
            "temperature": self.settings.temperature,
            "repetition_penalty": self.settings.repetition_penalty,
            "reference_configured": all(p.is_file() for p in self.settings.references),
            "reference_count": len(self.settings.references),
            "conditioning_seconds": 6 * len(self.settings.references),
            "max_segment_chars": 180,
            "sampling": "greedy",
        }


def create_app(settings: VoiceSettings | None = None):
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import Response

    voice = XttsVoice(settings or VoiceSettings.from_environment())

    @asynccontextmanager
    async def lifespan(_app):
        await asyncio.to_thread(voice.load)
        yield

    app = FastAPI(title="QHOS Brain Voice", docs_url=None, redoc_url=None, lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return voice.snapshot()

    @app.post("/v1/synthesize")
    def synthesize(body: dict) -> Response:
        try:
            audio = voice.synthesize(body.get("text", ""))
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid_text")
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc))
        return Response(content=audio, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Local XTTS voice service for Brain")
    parser.add_argument("--host", default="127.0.0.1", type=loopback_host)
    parser.add_argument("--port", type=int, default=8092)
    args = parser.parse_args(argv)
    import uvicorn

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
