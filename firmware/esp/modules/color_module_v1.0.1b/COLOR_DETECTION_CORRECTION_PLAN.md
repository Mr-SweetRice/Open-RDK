# Color detection correction plan

## Compatibility boundary

- Keep `color_module` v1.0.0 paired with Color Studio v1.0 and do not add new behavior there.
- Pair `color_module` v1.0.1a with Color Studio v1.1a.
- Report firmware version, expected page ID, and expected page version during the framed connection handshake and in `GET INFO`.
- Let the host select the matching page from its page registry. The page must independently warn when its ID/version differs from the selected module's expectation.

## Measurement and exposure

1. Record raw R/G/B/C, gain, integration time, LED state, saturation, and timestamp for every sample used by calibration or classification.
2. Freeze exposure while capturing DARK, BRIGHT, or a color patch. Reject a capture if exposure changes inside the sample set.
3. Convert readings to a common exposure domain before comparisons: subtract the dark reference, then compensate for gain and integration time.
4. Detect clipping and insufficient signal. A clipped BRIGHT reference or a DARK reference dominated by noise must be rejected with a specific diagnostic.
5. Verify LED settling time and discard the first sample after any LED or exposure transition.

## Reference normalization

1. Require valid DARK and BRIGHT references before color-patch capture.
2. Normalize each channel independently using `(sample - dark) / (bright - dark)` instead of assuming the BRIGHT surface is spectrally neutral.
3. Guard every channel denominator with a minimum span and report which channel has inadequate calibration range.
4. Preserve unclamped intermediate values for diagnostics; clamp only the values used by display/classification.
5. Calculate luma from the corrected channels and keep chroma separate from intensity.

## Neutral-color separation

1. Use editable black and bright luma thresholds as explicit gates before chromatic classification.
2. Require low chroma as well as suitable luma before calling a sample neutral. This prevents dark green from becoming black and bright green from becoming bright/white solely because of intensity.
3. Learn or configure a neutral-axis tolerance from DARK/BRIGHT captures so sensor-specific green bias does not masquerade as green color.
4. Between the two neutral gates, classify colors using chromatic distance and use luma only as a secondary distance term.
5. Expose the threshold values and the reason for the final decision in telemetry for tuning.

## Color classification

1. Compare corrected normalized RGB and Lab classifiers against the same exposure-normalized samples.
2. Store prototype spread/variance, not only the mean, and scale distance by observed capture noise.
3. Use the best-versus-second-best margin together with absolute distance for confidence.
4. Return unclassified when signal quality, calibration span, chroma, or confidence is inadequate.
5. Keep black at slot 0 and bright/white at slot 1 for all palettes so neutral gates have stable targets.

## Validation

1. Test both physical modules with the same black, bright, dark-green, bright-green, and mid-green patches at several distances.
2. Repeat after power cycles and verify persisted thresholds and calibration version migration.
3. Build a captured dataset covering all gain/integration steps, then replay it in unit tests.
4. Measure confusion matrices separately for neutral colors and chromatic colors.
5. Accept the algorithm only when green patches remain green across their useful luma range and neutral patches no longer inherit the sensor's green bias.

## v1.0.1a scope

This experimental release establishes version negotiation, freezes automatic exposure during calibration capture, and implements chroma-aware neutral-axis evidence. The calibrated black and white color patches define a line in Lab space. Every calibrated prototype always remains in the same competition. Black and white receive only a proportional distance penalty for departure from the neutral axis; there is no fixed neutral penalty that could exceed the measured black/dark-green separation. Editable black/bright values add endpoint penalties but never remove chromatic candidates. All displayed candidate and selected-color confidence values use the same monotonic distance score. Variance-aware prototypes, stored exposure metadata/compensation, adaptive neutral tolerance, and replay validation remain staged work for captured-data verification.

Normalized channel values above the BRIGHT reference are preserved instead of being independently clipped. Before Lab conversion they are scaled together by the largest channel, retaining chromatic ratios when automatic exposure produces readings above the reference capture range.
