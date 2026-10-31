"""
generate_demo_video.py
Creates a short synthetic traffic video with two "vehicles" (colored
rectangles) that drive toward each other and collide. Useful for
demoing/testing the detection pipeline end-to-end without needing to
source a real accident-video dataset first.

Usage:
    python generate_demo_video.py
Produces: demo_crash.mp4 in this folder (upload it via the dashboard).
"""

import cv2
import numpy as np
import os

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_crash.mp4")
WIDTH, HEIGHT = 640, 360
FPS = 25
DURATION_SEC = 8


def main():
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(OUT_PATH, fourcc, FPS, (WIDTH, HEIGHT))

    total_frames = FPS * DURATION_SEC

    # Car A moves left -> right, Car B moves right -> left in the SAME lane
    # (same y-band) so their bounding boxes actually overlap at collision.
    car_a_w, car_a_h = 60, 40
    car_b_w, car_b_h = 60, 40
    lane_y = HEIGHT // 2 - car_a_h // 2

    collide_frame = int(total_frames * 0.55)

    for f in range(total_frames):
        frame = np.full((HEIGHT, WIDTH, 3), 40, dtype=np.uint8)  # dark road background
        cv2.rectangle(frame, (0, HEIGHT // 2 - 60), (WIDTH, HEIGHT // 2 + 60), (60, 60, 60), -1)
        for ly in (HEIGHT // 2 - 20, HEIGHT // 2 + 20):
            for x in range(0, WIDTH, 40):
                cv2.line(frame, (x, ly), (x + 20, ly), (200, 200, 200), 2)

        progress = min(f, collide_frame) / collide_frame

        # Both cars converge toward the exact center and overlap there.
        center = WIDTH / 2
        ax = int(20 + progress * (center - car_a_w / 2 - 20))
        ay = lane_y
        bx = int(WIDTH - 20 - car_b_w - progress * (WIDTH - 20 - car_b_w - (center + car_b_w / 2)))
        by = lane_y

        if f > collide_frame:
            jitter = np.random.randint(-2, 3)
            ax += jitter
            bx += jitter

        cv2.rectangle(frame, (ax, ay), (ax + car_a_w, ay + car_a_h), (0, 140, 255), -1)
        cv2.rectangle(frame, (bx, by), (bx + car_b_w, by + car_b_h), (255, 100, 0), -1)

        writer.write(frame)

    writer.release()
    print(f"Demo video written to: {OUT_PATH}")
    print(f"Collision staged around frame {collide_frame} (~{collide_frame / FPS:.1f}s)")


if __name__ == "__main__":
    main()
