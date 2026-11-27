# Reolink Motion Boxes - 1.2.1 (candidate)

The metadata path reads BcMedia additional headers via a dedicated authenticated Baichuan TCP/9000 preview (command 3, standalone channel 0). It prefers substream, can try main when substream fails, and persists confirmed profile evidence against the camera configuration fingerprint. ONVIF remains available, with preference for previously proven ONVIF boxes. No arbitrary control command, firmware/configuration modification, new video decoder, FFmpeg process, local AI, or separate media file. The compressed H.264/H.265/audio payload is discarded; the preview does increase LAN traffic and camera session count. `liblz4.so.1` is used for bounded metadata decompression (system dependency liblz4-1, not an AI dependency).

The implementation handles the documented TLV envelope and LZ4 frame branch, person/vehicle/animal boxes, coordinates normalized using the transmitted AI-frame size or the documented 896x480 default. Does not infer classes, faces, plates, identities, or stable tracks from video. IDs for these boxes are local to the observation, and trails are disabled for them. Face/plate support remains limited to genuine metadata from the existing ONVIF path. Native event flags still use the existing camera event transport. Unknown/unsupported BcMedia variants fail closed; the parser does not implement every encryption scheme, multiplexed NVR/hub, multi-lens or alternate channel. No device-level evidence from user's hardware; preview framing tested on a simulator, actual compressed box fixture from upstream research. Timestamp is anchored to the I-frame camera epoch and media tick where compatible with NVR time; otherwise receipt time, marked on observation. Independent RTSP recording/playback pipelines and the live low-resolution buffer mean frame-perfect synchronization is not established. Archive search uses recorded native observations, not video pixels. Old recordings lacking stored metadata are not backfilled. The existing history and retention policy remain unchanged.

Protocol and capture provenance (MIT): https://github.com/apocaliss92/nodelink-js (reviewed detection.ts, aiClassMap.ts, BcMediaParser.ts, protocol/xml.ts, protocol/framing.ts, ReolinkBaichuanApi.ts and test/lib/detection-decoder.test.ts). Test fixture PFRAME_200_PEOPLE_BOX_HEX and empty header derived from that repository. The Python implementation is limited to the above supported subset.

MIT License

Copyright (c) 2026 apocaliss92

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
