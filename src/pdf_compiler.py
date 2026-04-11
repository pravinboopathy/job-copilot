"""pdflatex compilation wrapper."""

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def get_page_count(pdf_path: Path) -> int:
    """Count pages in a PDF.

    Uses pdfinfo if available, falls back to scanning PDF binary for page count.
    Returns -1 if page count cannot be determined.
    """
    # Try pdfinfo first
    if shutil.which("pdfinfo"):
        try:
            result = subprocess.run(
                ["pdfinfo", str(pdf_path)],
                capture_output=True,
                text=True,
                timeout=10,
            )
            for line in result.stdout.splitlines():
                if line.startswith("Pages:"):
                    return int(line.split(":")[1].strip())
        except (subprocess.TimeoutExpired, ValueError):
            pass

    # Fallback: scan PDF binary for /Count entries
    try:
        content = pdf_path.read_bytes()
        matches = re.findall(rb"/Count\s+(\d+)", content)
        if matches:
            return max(int(m) for m in matches)
    except OSError:
        pass

    return -1


def check_pdflatex() -> bool:
    """Check if pdflatex is available in PATH."""
    return shutil.which("pdflatex") is not None


def compile_to_pdf(tex_content: str, output_path: Path) -> Path | None:
    """Compile LaTeX to PDF using pdflatex.

    Writes tex to a temp directory, runs pdflatex twice (for references),
    copies PDF to output_path, and cleans up auxiliary files.

    Returns the PDF path on success, None on failure.
    """
    if not check_pdflatex():
        logger.error(
            "pdflatex not found. Install it with:\n"
            "  macOS:  brew install basictex\n"
            "  Linux:  apt install texlive"
        )
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        tex_file = tmp / "resume.tex"
        tex_file.write_text(tex_content, encoding="utf-8")

        for pass_num in (1, 2):
            try:
                result = subprocess.run(
                    [
                        "pdflatex",
                        "-interaction=nonstopmode",
                        "-halt-on-error",
                        "-output-directory",
                        str(tmp),
                        str(tex_file),
                    ],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                if result.returncode != 0:
                    logger.error("pdflatex pass %d failed:\n%s", pass_num, result.stdout[-2000:])
                    return None
            except subprocess.TimeoutExpired:
                logger.error("pdflatex pass %d timed out", pass_num)
                return None

        pdf_file = tmp / "resume.pdf"
        if not pdf_file.exists():
            logger.error("pdflatex produced no PDF output")
            return None

        output_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_file, output_path)
        logger.info("PDF compiled: %s", output_path)
        return output_path
