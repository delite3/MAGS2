import math
import tempfile
import unittest
from pathlib import Path

from sim_trajectory import load_trajectory_csv, unreal_rotator_quaternion
from ue_udp_sender import path_pose


class TrajectoryTests(unittest.TestCase):
    def assertQuaternionAlmostEqual(self, actual, expected):
        for actual_value, expected_value in zip(actual, expected):
            self.assertAlmostEqual(actual_value, expected_value)

    def write_csv(self, contents: str) -> Path:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        path = Path(temporary_directory.name) / "trajectory.csv"
        path.write_text(contents, encoding="utf-8")
        return path

    def test_unreal_rotator_axis_signs(self):
        half_sqrt = math.sqrt(0.5)
        cases = (
            ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
            ((90.0, 0.0, 0.0), (-half_sqrt, 0.0, 0.0, half_sqrt)),
            ((0.0, 90.0, 0.0), (0.0, -half_sqrt, 0.0, half_sqrt)),
            ((0.0, 0.0, 90.0), (0.0, 0.0, half_sqrt, half_sqrt)),
        )
        for euler_degrees, expected in cases:
            with self.subTest(euler_degrees=euler_degrees):
                self.assertQuaternionAlmostEqual(
                    unreal_rotator_quaternion(*euler_degrees), expected
                )

    def test_builtin_path_accepts_orientation_controls(self):
        position, quaternion = path_pose(
            "hover",
            elapsed_s=0.0,
            speed_mps=0.0,
            radius_m=0.0,
            period_s=1.0,
            object_height_m=3.0,
            roll_deg=10.0,
            pitch_deg=-5.0,
            yaw_offset_deg=25.0,
        )
        self.assertEqual(position, (0.0, 0.0, 3.0))
        self.assertQuaternionAlmostEqual(
            quaternion, unreal_rotator_quaternion(10.0, -5.0, 25.0)
        )

    def test_csv_interpolates_position_and_shortest_orientation_arc(self):
        path = self.write_csv(
            "time_s,x_m,y_m,z_m,roll_deg,pitch_deg,yaw_deg\n"
            "0,0,0,2,0,0,350\n"
            "2,4,2,4,0,0,10\n"
        )
        trajectory = load_trajectory_csv(path)

        position, quaternion = trajectory.pose_at(1.0)

        self.assertEqual(position, (2.0, 1.0, 3.0))
        self.assertAlmostEqual(quaternion[0], 0.0)
        self.assertAlmostEqual(quaternion[1], 0.0)
        self.assertAlmostEqual(quaternion[2], 0.0, places=7)
        self.assertAlmostEqual(abs(quaternion[3]), 1.0)

    def test_csv_holds_first_and_last_pose(self):
        path = self.write_csv(
            "time_s,x_m,y_m,z_m,roll_deg,pitch_deg,yaw_deg\n"
            "0,1,2,3,0,0,0\n"
            "4,5,6,7,0,0,90\n"
        )
        trajectory = load_trajectory_csv(path)

        self.assertEqual(trajectory.pose_at(-1.0), trajectory.pose_at(0.0))
        self.assertEqual(trajectory.pose_at(5.0), trajectory.pose_at(4.0))

    def test_csv_requires_complete_header(self):
        path = self.write_csv("time_s,x_m\n0,0\n1,1\n")
        with self.assertRaisesRegex(ValueError, "missing CSV columns"):
            load_trajectory_csv(path)

    def test_csv_requires_zero_start_and_increasing_time(self):
        nonzero_start = self.write_csv(
            "time_s,x_m,y_m,z_m,roll_deg,pitch_deg,yaw_deg\n"
            "1,0,0,0,0,0,0\n"
            "2,1,0,0,0,0,0\n"
        )
        with self.assertRaisesRegex(ValueError, "first trajectory sample"):
            load_trajectory_csv(nonzero_start)

        repeated_time = self.write_csv(
            "time_s,x_m,y_m,z_m,roll_deg,pitch_deg,yaw_deg\n"
            "0,0,0,0,0,0,0\n"
            "0,1,0,0,0,0,0\n"
        )
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            load_trajectory_csv(repeated_time)


if __name__ == "__main__":
    unittest.main()
