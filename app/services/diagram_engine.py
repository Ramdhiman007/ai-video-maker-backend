import math
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from PIL import Image, ImageDraw, ImageFont

from app.config import FFMPEG_PATH


class DiagramEngine:
    """
    Renders programmatic animated diagrams, architectural flowcharts,
    and process explanation video clips at 30 fps.
    """

    def generate_diagram_clip(
        self,
        scene_spec: Dict[str, Any],
        duration: float,
        out_path: Path,
        width: int = 1920,
        height: int = 1080,
        fps: int = 15
    ) -> Path:
        import gc
        import shutil

        out_path.parent.mkdir(parents=True, exist_ok=True)
        frames_dir = out_path.parent / f"diag_frames_{out_path.stem}"
        frames_dir.mkdir(parents=True, exist_ok=True)

        fps = min(fps, 15)
        c_w = min(width, 1280)
        c_h = min(height, 720)

        total_frames = max(int(duration * fps), 15)
        narration = scene_spec.get("narration", "")
        diag_spec = scene_spec.get("diagram_spec") or {}

        # Extract nodes and arrows or build default based on narration
        nodes = diag_spec.get("nodes") or self._infer_nodes_from_narration(narration)
        arrows = diag_spec.get("arrows") or ["->"] * (len(nodes) - 1)
        diagram_title = diag_spec.get("title") or self._infer_title_from_narration(narration)

        # Generate frames with moving pulses along connection lines
        for frame_idx in range(total_frames):
            img = Image.new("RGB", (c_w, c_h), (15, 23, 42))  # Slate dark canvas
            draw = ImageDraw.Draw(img)

            # Header & Background Grid
            self._draw_grid_backdrop(draw, c_w, c_h)

            # Diagram Banner
            draw.text((c_w // 2 - 180, 45), diagram_title, fill=(248, 250, 252))
            draw.text((c_w // 2 - 220, 80), narration[:80], fill=(148, 163, 184))

            # Calculate horizontal positions of nodes
            num_nodes = len(nodes)
            node_w = min(220, int((c_w * 0.75) / max(num_nodes, 1)))
            node_h = 80
            spacing = (c_w - 160 - (num_nodes * node_w)) // max(num_nodes - 1, 1)
            start_x = 80
            center_y = c_h // 2 - 15

            node_positions = []
            for i, node_text in enumerate(nodes):
                nx = start_x + i * (node_w + spacing)
                ny = center_y
                node_positions.append((nx, ny, nx + node_w, ny + node_h))

            # Draw connection lines & animated pulses
            t_ratio = frame_idx / total_frames
            for i in range(num_nodes - 1):
                p1 = node_positions[i]
                p2 = node_positions[i + 1]

                x1 = p1[2]
                y1 = p1[1] + node_h // 2
                x2 = p2[0]
                y2 = p2[1] + node_h // 2

                # Line
                draw.line([(x1, y1), (x2, y2)], fill=(51, 65, 85), width=3)

                # Arrowhead at target
                draw.polygon([(x2 - 12, y2 - 6), (x2, y2), (x2 - 12, y2 + 6)], fill=(99, 102, 241))

                # Animated signal pulse moving along the wire
                pulse_phase = (t_ratio * 4.0 + i * 0.3) % 1.0
                px = int(x1 + (x2 - x1) * pulse_phase)
                py = int(y1 + (y2 - y1) * pulse_phase)
                draw.ellipse([(px - 6, py - 6), (px + 6, py + 6)], fill=(56, 189, 248))
                draw.ellipse([(px - 3, py - 3), (px + 3, py + 3)], fill=(255, 255, 255))

            # Draw node boxes with dynamic highlight
            active_node_idx = min(num_nodes - 1, int(t_ratio * num_nodes))
            for i, (nx1, ny1, nx2, ny2) in enumerate(node_positions):
                is_active = (i == active_node_idx)
                box_bg = (30, 41, 59) if not is_active else (49, 46, 129)
                box_border = (99, 102, 241) if is_active else (71, 85, 105)
                border_w = 2 if is_active else 1

                draw.rounded_rectangle([(nx1, ny1), (nx2, ny2)], radius=12, fill=box_bg, outline=box_border, width=border_w)

                # Step Badge (e.g. 01, 02)
                badge_text = f"{i+1:02d}"
                draw.rounded_rectangle([(nx1 + 8, ny1 + 8), (nx1 + 36, ny1 + 30)], radius=6, fill=(99, 102, 241) if is_active else (51, 65, 85))
                draw.text((nx1 + 14, ny1 + 11), badge_text, fill=(255, 255, 255))

                # Node Label
                label = nodes[i]
                draw.text((nx1 + 44, ny1 + 11), label[:18], fill=(248, 250, 252))
                sub_label = "Active Component" if is_active else "Standby / Verified"
                draw.text((nx1 + 12, ny1 + 42), sub_label, fill=(52, 211, 153) if is_active else (148, 163, 184))

            # Bottom status banner
            draw.rounded_rectangle([(c_w // 2 - 240, c_h - 110), (c_w // 2 + 240, c_h - 60)], radius=10, fill=(30, 41, 59), outline=(71, 85, 105))
            draw.text((c_w // 2 - 200, c_h - 92), f"Flow Status: Executing Stage {active_node_idx + 1} of {num_nodes} ... Verified", fill=(52, 211, 153))

            frame_file = frames_dir / f"df_{frame_idx:04d}.png"
            img.save(frame_file)
            del img

        # Assemble via FFmpeg
        cmd = [
            FFMPEG_PATH, "-y",
            "-threads", "1",
            "-framerate", str(fps),
            "-i", str(frames_dir / "df_%04d.png"),
            "-t", str(duration),
            "-vf", f"scale={width}:{height}:flags=bilinear,fps={fps}",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-threads", "1",
            "-pix_fmt", "yuv420p",
            str(out_path),
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

        shutil.rmtree(frames_dir, ignore_errors=True)
        gc.collect()
        return out_path

    def _draw_grid_backdrop(self, draw, width: int, height: int):
        grid_step = 60
        for x in range(0, width, grid_step):
            draw.line([(x, 0), (x, height)], fill=(22, 30, 46), width=1)
        for y in range(0, height, grid_step):
            draw.line([(0, y), (width, y)], fill=(22, 30, 46), width=1)

    def _infer_nodes_from_narration(self, narration: str) -> List[str]:
        n_low = narration.lower()
        if "sql" in n_low or "database" in n_low:
            return ["Application Client", "Backend Query Runner", "PostgreSQL Engine", "Assertion Result"]
        elif "api" in n_low or "request" in n_low or "server" in n_low:
            return ["HTTP Request", "API Gateway", "Microservice", "Database Record"]
        elif "windows" in n_low or "fix" in n_low or "search" in n_low:
            return ["Issue Detected", "System Services", "Registry Settings", "Resolved State"]
        elif "playwright" in n_low or "test" in n_low or "selenium" in n_low:
            return ["Test Runner CLI", "Browser Driver", "Page Actions", "Test Report"]
        else:
            return ["Input Concept", "Processing Engine", "Optimization", "Final Delivery"]

    def _infer_title_from_narration(self, narration: str) -> str:
        words = narration.split()
        return " ".join(words[:6]).title() if words else "System Architecture Flow"
