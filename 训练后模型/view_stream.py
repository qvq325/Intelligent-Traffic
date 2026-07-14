"""纯流预览 — 无检测，只看画面"""
import cv2, sys

src = sys.argv[1] if len(sys.argv) > 1 else 0
if isinstance(src, str) and src.isdigit():
    src = int(src)

cap = cv2.VideoCapture(src)
if not cap.isOpened():
    print(f"无法打开: {src}")
    sys.exit(1)

w, h = int(cap.get(3)), int(cap.get(4))
fps = cap.get(cv2.CAP_PROP_FPS) or 30
print(f"分辨率: {w}x{h}  FPS: {fps}  按 Q 退出")

cv2.namedWindow("Stream", cv2.WINDOW_NORMAL)
cv2.resizeWindow("Stream", min(w, 1280), int(min(w, 1280) / w * h))

while True:
    ret, frame = cap.read()
    if not ret:
        print("流断开")
        break
    cv2.imshow("Stream", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()
