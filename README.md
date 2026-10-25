# youtube-shorts-publisher
Auto-publish YouTube Shorts via manual workflow dispatch

## Reviewed long-video delivery branch

Branch `upload/crm-or-erp-dz-reviewed` also supports a reviewed landscape video.
The existing workflow path accepts `transfer_tag` and `mode` on that branch:
`preflight` verifies hashes, the expected channel and duplicate titles; `upload`
creates a video once and attaches its reviewed thumbnail; `verify` reuses the
stored video ID. Upload receipts persist in a draft release for rerun protection.
An ambiguous started upload must be inspected before any retry.

Credentials use the existing `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`,
`YOUTUBE_REFRESH_TOKEN` repository secrets; never put their values in source or
workflow inputs. Tag ordering and Arabic locale canonicalization are recorded
explicitly. The service worker saves the served thumbnail for visual review.
The default branch's original manual URL workflow is unchanged.

Completed owner-approved delivery: https://youtu.be/5VlSLG2i2Gc (unlisted).
Final verification run:40146570840; the full source/receipts handoff is maintained
on `mohammedbenchaa/somuraqami-site`, branch `video/crm-or-erp-bilingual`.
