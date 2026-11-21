"""Comprehensive live system audit exercising every engine in Mewvia."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
from pathlib import Path

from app.config import get_settings
from app.media.audio_tools import (
    change_audio_speed,
    convert_to_mp3,
    convert_to_voice_note,
    extract_audio_from_video,
)
from app.media.downloader import (
    _download_audio_sync,
    _download_video_sync,
    _probe_info_sync,
    download_spotify_track,
    extract_spotify_metadata_sync,
)
from app.media.filters import apply_filter_to_image
from app.media.kinds import MediaKind
from app.media.lyrics import fetch_lyrics
from app.media.pipeline import convert
from app.media.probe import probe
from app.media.shazam import identify_music_from_file
from app.media.specs import TARGET_WEBM_STICKER, TARGET_WEBP
from app.media.text_overlay import add_text_to_image
from app.media.video_tools import compress_video, make_video_note, mute_video, reverse_video
from app.media.wide import make_image_wide


async def audit() -> None:
    settings = get_settings()
    results: list[tuple[str, bool, str]] = []

    print("=" * 60)
    print("🚀 STARTING COMPREHENSIVE MEWVIA SYSTEM AUDIT")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)

        # 1. Generate Synthetic Test Media
        test_video = work / "test_video.mp4"
        test_audio = work / "test_audio.mp3"
        test_image = work / "test_image.png"

        # Generate a 3-second test MP4 with audio
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=duration=3:size=640x360:rate=30",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=3",
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-pix_fmt",
                "yuv420p",
                str(test_video),
            ],
            capture_output=True,
            check=True,
        )

        # Generate test MP3
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=880:duration=3",
                "-c:a",
                "libmp3lame",
                str(test_audio),
            ],
            capture_output=True,
            check=True,
        )

        # Generate test PNG
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=blue:s=512x512:d=1",
                "-vframes",
                "1",
                str(test_image),
            ],
            capture_output=True,
            check=True,
        )

        # --- TEST 1: Video Tools: Video Note (Round Circle 1:1) ---
        try:
            info = await probe(test_video)
            vn_out = work / "vnote.mp4"
            await make_video_note(test_video, vn_out, info)
            vn_info = await probe(vn_out)
            assert vn_info.width == 512 and vn_info.height == 512
            results.append(
                ("Video Note (1:1 Square Round)", True, f"{vn_info.width}x{vn_info.height}")
            )
        except Exception as e:
            results.append(("Video Note (1:1 Square Round)", False, str(e)))

        # --- TEST 2: Video Tools: Compression ---
        try:
            cmp_out = work / "compressed.mp4"
            await compress_video(test_video, cmp_out, info, level="medium")
            assert cmp_out.is_file() and cmp_out.stat().st_size > 0
            results.append(("Video Compression", True, f"Size: {cmp_out.stat().st_size}b"))
        except Exception as e:
            results.append(("Video Compression", False, str(e)))

        # --- TEST 3: Video Tools: Mute ---
        try:
            mute_out = work / "muted.mp4"
            await mute_video(test_video, mute_out)
            mute_info = await probe(mute_out)
            assert not mute_info.has_audio
            results.append(("Video Mute (Remove Audio)", True, "Audio stripped"))
        except Exception as e:
            results.append(("Video Mute (Remove Audio)", False, str(e)))

        # --- TEST 4: Video Tools: Reverse ---
        try:
            rev_out = work / "reversed.mp4"
            await reverse_video(test_video, rev_out, info)
            assert rev_out.is_file() and rev_out.stat().st_size > 0
            results.append(("Video Reverse", True, "Reversed successfully"))
        except Exception as e:
            results.append(("Video Reverse", False, str(e)))

        # --- TEST 5: Video Tools: Extract Audio ---
        try:
            ext_out = work / "extracted.mp3"
            await extract_audio_from_video(test_video, ext_out)
            ext_info = await probe(ext_out)
            assert ext_info.has_audio
            results.append(
                ("Extract Audio from Video", True, f"MP3 created ({ext_info.duration}s)")
            )
        except Exception as e:
            results.append(("Extract Audio from Video", False, str(e)))

        # --- TEST 6: Audio Tools: Convert to Voice Note (OGG Opus) ---
        try:
            voice_out = work / "voice.ogg"
            await convert_to_voice_note(test_audio, voice_out)
            v_info = await probe(voice_out)
            assert v_info.audio_codec == "opus"
            results.append(("Music to Voice Note (Opus)", True, "OGG Opus generated"))
        except Exception as e:
            results.append(("Music to Voice Note (Opus)", False, str(e)))

        # --- TEST 7: Audio Tools: Convert Voice to MP3 ---
        try:
            mp3_out = work / "conv.mp3"
            await convert_to_mp3(voice_out, mp3_out)
            m_info = await probe(mp3_out)
            assert m_info.audio_codec == "mp3"
            results.append(("Voice to MP3 Audio", True, "MP3 generated"))
        except Exception as e:
            results.append(("Voice to MP3 Audio", False, str(e)))

        # --- TEST 8: Audio Tools: Speed Changer (1.5x) ---
        try:
            spd_out = work / "speed.mp3"
            await change_audio_speed(test_audio, spd_out, speed_factor=1.5, to_voice=False)
            s_info = await probe(spd_out)
            assert s_info.duration < 2.5
            results.append(
                ("Audio Speed Change (1.5x)", True, f"New duration: {s_info.duration:.2f}s")
            )
        except Exception as e:
            results.append(("Audio Speed Change (1.5x)", False, str(e)))

        # --- TEST 9: Cross-Media: Photo to Sticker (WEBP) ---
        try:
            conv_out = await convert(test_image, MediaKind.PHOTO, TARGET_WEBP, work)
            assert conv_out.path.is_file() and conv_out.path.suffix == ".webp"
            results.append(("Photo to Sticker (WEBP)", True, f"Output: {conv_out.path.name}"))
        except Exception as e:
            results.append(("Photo to Sticker (WEBP)", False, str(e)))

        # --- TEST 10: Cross-Media: Video to Video Sticker (WEBM) ---
        try:
            v_stk_out = await convert(test_video, MediaKind.VIDEO, TARGET_WEBM_STICKER, work)
            stk_info = await probe(v_stk_out.path)
            assert stk_info.video_codec == "vp9" and not stk_info.has_audio
            results.append(("Video to Video Sticker (VP9)", True, "VP9 silent WEBM sticker"))
        except Exception as e:
            results.append(("Video to Video Sticker (VP9)", False, str(e)))

        # --- TEST 11: Text Overlay / Watermark (Persian text on image) ---
        try:
            txt_out = work / "overlay.png"
            await add_text_to_image(
                src_path=test_image,
                out_path=txt_out,
                text="میوویا دستیار مدیا",
                font_key="vazir",
                size_key="medium",
                color_key="white",
                pos_key="bottom",
            )
            assert txt_out.is_file() and txt_out.stat().st_size > 0
            results.append(("Text Overlay (استیکرمانی)", True, "Persian text overlaid"))
        except Exception as e:
            results.append(("Text Overlay (استیکرمانی)", False, str(e)))

        # --- TEST 12: Wide Distortion (پهنش کن!) ---
        try:
            wide_out = work / "wide.png"
            await make_image_wide(test_image, wide_out)
            w_info = await probe(wide_out)
            assert w_info.width > w_info.height
            results.append(
                ("Wide Transform (پهنش کن)", True, f"Stretched to {w_info.width}x{w_info.height}")
            )
        except Exception as e:
            results.append(("Wide Transform (پهنش کن)", False, str(e)))

        # --- TEST 13: Filters (Black & White, Negative) ---
        try:
            flt_out = work / "filter_bw.png"
            await apply_filter_to_image(test_image, flt_out, filter_id="flt_bw")
            assert flt_out.is_file() and flt_out.stat().st_size > 0
            results.append(("Filters (Black & White)", True, "B&W filter applied"))
        except Exception as e:
            results.append(("Filters (Black & White)", False, str(e)))

        # --- TEST 14: Downloader: Instagram Reel Live Extraction (Zero-Cookie) ---
        try:
            ig_url = "https://www.instagram.com/reel/DeO_JgKIVYt/"
            ig_info = _probe_info_sync(ig_url)
            assert ig_info.is_video and ig_info.platform_key == "instagram"
            results.append(
                ("Instagram Reel Probe (Zero-Cookie)", True, f"Title: {ig_info.title[:25]}...")
            )
        except Exception as e:
            results.append(("Instagram Reel Probe (Zero-Cookie)", False, str(e)))

        # --- TEST 15: Downloader: Spotify Metadata & Embed Extraction ---
        try:
            sp_url = "https://open.spotify.com/track/4cOdK2wGLETKBW3PvgPWqT"
            sp_meta = extract_spotify_metadata_sync(sp_url)
            assert sp_meta.get("title")
            results.append(("Spotify Metadata Extraction", True, f"Track: {sp_meta.get('title')}"))
        except Exception as e:
            results.append(("Spotify Metadata Extraction", False, str(e)))

        # --- TEST 16: Downloader: Spotify Track Download with SoundCloud Fallback ---
        try:
            sp_dl = await download_spotify_track(
                title="Coldplay Yellow",
                artist="Coldplay",
                thumbnail_url="https://i.scdn.co/image/ab67616d0000b27341074e2d2740fcb212f45532",
                work_dir=work,
                max_bytes=settings.upload_limit,
            )
            assert sp_dl.is_file() and sp_dl.stat().st_size > 1_000_000
            results.append(
                (
                    "Spotify Track Download & Tagging",
                    True,
                    f"Downloaded: {sp_dl.stat().st_size // 1024} KB",
                )
            )
        except Exception as e:
            results.append(("Spotify Track Download & Tagging", False, str(e)))

        # --- TEST 17: Shazam Acoustic Identification ---
        try:
            shazam_res = await identify_music_from_file(sp_dl, work)
            assert shazam_res is not None and "coldplay" in shazam_res.artist.lower()
            results.append(
                (
                    "Shazam Music Identification",
                    True,
                    f"Identified: {shazam_res.title} by {shazam_res.artist}",
                )
            )
        except Exception as e:
            results.append(("Shazam Music Identification", False, str(e)))

        # --- TEST 18: Lyrics Engine (LRCLIB & Format) ---
        try:
            lyrics = await fetch_lyrics("Yellow", "Coldplay")
            assert lyrics and "Look at the stars" in lyrics
            results.append(("Lyrics Search (LRCLIB)", True, "Found & extracted lyrics"))
        except Exception as e:
            results.append(("Lyrics Search (LRCLIB)", False, str(e)))

    print("\n" + "=" * 60)
    print("📊 COMPREHENSIVE AUDIT RESULTS SUMMARY")
    print("=" * 60)
    all_ok = True
    for name, status, details in results:
        status_str = "✅ PASS" if status else "❌ FAIL"
        if not status:
            all_ok = False
        print(f"[{status_str}] {name:38s} | {details}")
    print("=" * 60)
    if all_ok:
        print("🎉 ALL 18 CORE USER-FACING PIPELINES ARE 100% HEALTHY & FUNCTIONAL!")
    else:
        print("⚠️ SOME PIPELINES FAILED — SEE DETAILS ABOVE")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(audit())
