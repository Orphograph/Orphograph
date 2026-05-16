// exif.js — minimal client-side JPEG EXIF extractor.
//
// Parses the EXIF APP1 segment of a JPEG file to surface camera + capture
// metadata as corroborating evidence in the anchored receipt. GPS tags are
// DELIBERATELY DROPPED to avoid leaking shoot locations into the public
// receipt JSON (the user can re-add GPS via a separate attestation if they
// want).
//
// Returns: {
//   exif_camera_make, exif_camera_model, exif_camera_serial,
//   exif_lens, exif_capture_time, exif_software,
//   exif_iso, exif_aperture, exif_shutter, exif_focal_length,
//   image_width, image_height, image_format, size_bytes, filename, mime_type
// }
// Any field that can't be parsed is omitted.
//
// Stdlib-equivalent: no external libraries. ~200 lines.

(function (global) {
  "use strict";

  // EXIF tag IDs we care about (decimal).
  const TAGS = {
    0x010F: "exif_camera_make",
    0x0110: "exif_camera_model",
    0x0131: "exif_software",
    0x0132: "exif_capture_time",      // ModifyDate (fallback)
    0x9003: "exif_capture_time",      // DateTimeOriginal (preferred)
    0xA434: "exif_lens",              // LensModel
    0x8827: "exif_iso",
    0x829D: "exif_aperture",          // FNumber
    0x829A: "exif_shutter",           // ExposureTime
    0x920A: "exif_focal_length",
    0xA431: "exif_camera_serial",     // BodySerialNumber
    0xA432: "exif_lens_specs",
    0xA002: "exif_pixel_width",
    0xA003: "exif_pixel_height",
  };
  // GPS-related tags (0x8825 = GPS IFD pointer) are deliberately not in TAGS.

  function readU16(view, off, little) {
    return view.getUint16(off, little);
  }
  function readU32(view, off, little) {
    return view.getUint32(off, little);
  }

  function readAscii(view, off, count) {
    const bytes = [];
    for (let i = 0; i < count; i++) {
      const b = view.getUint8(off + i);
      if (b === 0) break;
      bytes.push(b);
    }
    return new TextDecoder("utf-8", { fatal: false }).decode(new Uint8Array(bytes));
  }

  function readRational(view, off, little) {
    const num = readU32(view, off, little);
    const den = readU32(view, off + 4, little);
    if (!den) return 0;
    return num / den;
  }

  function parseIFD(view, ifdOffset, tiffOffset, little) {
    const entries = readU16(view, ifdOffset, little);
    const out = {};
    for (let i = 0; i < entries; i++) {
      const entryOff = ifdOffset + 2 + i * 12;
      const tag = readU16(view, entryOff, little);
      const type = readU16(view, entryOff + 2, little);
      const count = readU32(view, entryOff + 4, little);
      const valueOff = entryOff + 8;
      const fieldName = TAGS[tag];
      if (!fieldName) {
        // Follow EXIF SubIFD pointer (0x8769) to pick up DateTimeOriginal etc.
        if (tag === 0x8769) {
          const subOff = tiffOffset + readU32(view, valueOff, little);
          try {
            Object.assign(out, parseIFD(view, subOff, tiffOffset, little));
          } catch {}
        }
        continue;
      }
      try {
        if (type === 2) {
          // ASCII string
          const strOff = count > 4 ? tiffOffset + readU32(view, valueOff, little) : valueOff;
          out[fieldName] = readAscii(view, strOff, count).trim();
        } else if (type === 3) {
          // U16
          out[fieldName] = readU16(view, valueOff, little);
        } else if (type === 4) {
          // U32
          out[fieldName] = readU32(view, valueOff, little);
        } else if (type === 5) {
          // Rational (8 bytes — always points to offset)
          const ratOff = tiffOffset + readU32(view, valueOff, little);
          out[fieldName] = readRational(view, ratOff, little);
        }
      } catch {
        // Skip malformed tag, keep going.
      }
    }
    return out;
  }

  async function extractExif(file) {
    const result = {
      filename: file.name || "",
      size_bytes: file.size,
      mime_type: file.type || "",
    };
    // Track whether we actually parsed EXIF or just returned file-level meta.
    // Caller can inspect result._exif_status — "ok" | "skipped" | "failed" —
    // to surface in the UI whether metadata was anchored. Silent loss of
    // EXIF on a Creator-tier anchor is a product-breaking failure mode.
    result._exif_status = "skipped";
    result._exif_reason = "";

    if (!file.type || !file.type.startsWith("image/")) {
      result._exif_reason = "not an image MIME type";
      return result;
    }

    const slice = file.slice(0, Math.min(file.size, 256 * 1024));
    let buf;
    try {
      buf = await slice.arrayBuffer();
    } catch (e) {
      result._exif_status = "failed";
      result._exif_reason = "could not read file bytes: " + (e && e.message || e);
      console.warn("[orphograph/exif]", result._exif_reason);
      return result;
    }
    const view = new DataView(buf);
    if (view.byteLength < 4) {
      result._exif_reason = "file too small for EXIF header";
      return result;
    }

    // JPEG SOI marker: 0xFFD8
    if (view.getUint16(0) !== 0xFFD8) {
      // Non-JPEG (PNG, HEIC, etc.) — we only parse JPEG EXIF in this minimal
      // implementation. Image dimensions for PNG could be added later.
      result._exif_reason = "not a JPEG (no SOI marker); EXIF skipped";
      return result;
    }
    result.image_format = "jpeg";

    let offset = 2;
    while (offset < view.byteLength - 4) {
      if (view.getUint8(offset) !== 0xFF) break;
      const marker = view.getUint8(offset + 1);
      const segLength = view.getUint16(offset + 2);
      if (marker === 0xE1) {
        // APP1 — likely EXIF. Check "Exif\0\0" prefix.
        const headerOff = offset + 4;
        if (
          view.getUint8(headerOff) === 0x45 &&     // E
          view.getUint8(headerOff + 1) === 0x78 && // x
          view.getUint8(headerOff + 2) === 0x69 && // i
          view.getUint8(headerOff + 3) === 0x66 && // f
          view.getUint8(headerOff + 4) === 0x00 &&
          view.getUint8(headerOff + 5) === 0x00
        ) {
          const tiffOffset = headerOff + 6;
          const byteOrder = view.getUint16(tiffOffset);
          const little = byteOrder === 0x4949; // II = little-endian
          const ifd0 = tiffOffset + readU32(view, tiffOffset + 4, little);
          try {
            Object.assign(result, parseIFD(view, ifd0, tiffOffset, little));
            result._exif_status = "ok";
          } catch (e) {
            result._exif_status = "failed";
            result._exif_reason = "IFD parse error: " + (e && e.message || e);
            console.warn("[orphograph/exif]", result._exif_reason);
          }
          break;
        }
      }
      offset += 2 + segLength;
    }

    // Promote subset of EXIF dimensions if available.
    if (result.exif_pixel_width) result.image_width = result.exif_pixel_width;
    if (result.exif_pixel_height) result.image_height = result.exif_pixel_height;
    delete result.exif_pixel_width;
    delete result.exif_pixel_height;
    delete result.exif_lens_specs;

    return result;
  }

  global.OrphographExif = { extractExif };
})(typeof window !== "undefined" ? window : globalThis);
