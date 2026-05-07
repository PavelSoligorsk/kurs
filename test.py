from fast_alpr import ALPR

# You can also initialize the ALPR with custom plate detection and OCR models.
alpr = ALPR(
    detector_model="yolo-v9-t-384-license-plate-end2end",
    ocr_model="cct-xs-v2-global-model",
)

# The "assets/test_image.png" can be found in repo root dir
alpr_results = alpr.predict("debug_processed_0_1.jpg")
print(alpr_results)