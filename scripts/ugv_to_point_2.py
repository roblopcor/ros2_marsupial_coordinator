#!/usr/bin/env python3

import math
import rclpy
import numpy as np
from rclpy.node import Node
from geometry_msgs.msg import Pose
from std_msgs.msg import Float64MultiArray, Float64, Float32
from rclpy.clock import Clock, ClockType

class UGVController(Node):

    def __init__(self):
        super().__init__('ugv_controller')
        
        self.target_position = Pose()
        self.current_position = Pose()
        self.uav_position = Pose()
        self.target_length = 0.0
        self.use_tether_trayectory = True
        self.distance = 0.6
        self.radius = 0.1494115                  
        self.effective_radius = 0.1494115   

        self.target_uav_position = 0.0     

        self.tether_length = 0.5
        self.tether_coef = 1.05      
        self.safety_margin = -0.3           

        self.kp_winch = 2.5
        self.ki_winch = 0.0
        self.kd_winch = 0.1
        self.winch_position_x = -0.25
        self.winch_position_z = 0.35

        self.integral_error = 0.0
        self.previous_error = 0.0

        timer_period = 0.02

        #self.constant_speed = 0.5       
        self.tolerance = 0.1

        self.pos = np.array([0, 0, 0, 0], float)
        self.vel = np.array([0, 0, 0, 0, 0], float)

        self.clock = self.get_clock()
        self.last_time = self.clock.now().seconds_nanoseconds()[0] + self.clock.now().seconds_nanoseconds()[1] * 1e-9

        # Velocidad de referencia del UGV
        self.speed_subscriber = self.create_subscription(Float32, '/ugv/reference_speed', self.speed_callback, 10)

        self.pose_subscriber = self.create_subscription(Pose, '/rs_robot/ugv_gt_pose', self.pose_callback, 10)
        self.uav_pose_subscriber = self.create_subscription(Pose, '/sjtu_drone/gt_pose', self.uav_pose_callback, 10)
        self.target_subscriber = self.create_subscription(Pose, '/ugv/reference_pose', self.target_callback, 10)
        self.target_uav_subscriber = self.create_subscription(Pose, '/uav/reference_pose', self.target_uav_callback, 10)
        self.target_length_subscriber = self.create_subscription(Float64, '/cable_length', self.target_length_callback, 10)

        self.pub_pos = self.create_publisher(Float64MultiArray, '/forward_position_controller/commands', 10)
        self.pub_vel = self.create_publisher(Float64MultiArray, '/forward_velocity_controller/commands', 10)
        self.pub_cable_length = self.create_publisher(Float64MultiArray, '/cable_lengthPUB', 10)

        self.timer = self.create_timer(timer_period, self.control_loop)
        self.get_logger().info('UGV controller has been started.')

    def pose_callback(self, msg):
        self.current_position = msg

    def uav_pose_callback(self, msg):
        self.uav_position = msg

    def target_callback(self, msg):
        self.target_position = msg

    def target_uav_callback(self, msg):
        self.target_uav_position = msg

    def speed_callback(self, msg):
        self.desired_speed = msg.data

    def target_length_callback(self, msg):
        if self.use_tether_trayectory:
            # self.effective_radius = 0.07
            self.safety_margin = 0.0
            offset_ugv_z = 0.9
            offset_ugv_x = - 0.5
            distance = math.sqrt(
                (self.target_uav_position.position.x - (self.target_position.position.x + self.winch_position_x + offset_ugv_x)) ** 2 +
                (self.target_uav_position.position.y - self.target_position.position.y) ** 2 +
                (self.target_uav_position.position.z - (self.target_position.position.z + self.winch_position_z + offset_ugv_z)) ** 2
            )
            target_length_info = msg.data
            
            self.tether_coef = target_length_info / distance 
            # if self.tether_coef < 1.05:
            #     self.tether_coef = 1.05
            # self.get_logger().info(f'Distance: {distance:.5f}, coeficiente: {self.tether_coef:.5f}')

            

    def calculate_winch_velocity(self):
        self.distance = math.sqrt(
            (self.uav_position.position.x - (self.current_position.position.x + self.winch_position_x)) ** 2 +
            (self.uav_position.position.y - self.current_position.position.y) ** 2 +
            (self.uav_position.position.z - (self.current_position.position.z + self.winch_position_z)) ** 2
        )
        self.target_length = self.distance * self.tether_coef + self.safety_margin
        length_error = self.target_length - self.tether_length

        current_time = self.clock.now().seconds_nanoseconds()[0] + self.clock.now().seconds_nanoseconds()[1] * 1e-9
        dt = current_time - self.last_time
        self.last_time = current_time

        self.integral_error += length_error * dt
        derivative_error = (length_error - self.previous_error) / dt
        self.previous_error = length_error

        winch_velocity_linear = (self.kp_winch * length_error +
                      self.ki_winch * self.integral_error +
                      self.kd_winch * derivative_error)

        winch_velocity_angular = winch_velocity_linear / self.radius

        self.tether_length += winch_velocity_angular * self.effective_radius * dt

        self.get_logger().info(f'Vel linear: {winch_velocity_linear:.5f}, vel angular {winch_velocity_angular:.5f}, dt: {dt:.5f}')

        return winch_velocity_angular, length_error
    
    def control_loop(self):
        if self.current_position is None or self.uav_position is None or self.target_position is None:
            return        

        direction_x = self.target_position.position.x - self.current_position.position.x
        direction_y = self.target_position.position.y - self.current_position.position.y

        magnitude = math.sqrt(direction_x**2 + direction_y**2)

        vel_msg = Float64MultiArray()
        pos_msg = Float64MultiArray()
        cable_length_msg = Float64MultiArray()

        if magnitude < self.tolerance:
            control_xy = 0.0
        else:
            sign = np.sign(direction_x)
            control_xy = self.desired_speed * sign

        if direction_x != 0 and magnitude > self.tolerance:
            steering_angle = math.atan(direction_y/direction_x)
        else:
            steering_angle = 0.0

        winch_velocity, length_error = self.calculate_winch_velocity()

        pos_msg.data = [float(steering_angle), float(steering_angle), float(steering_angle), float(steering_angle)]
        vel_msg.data = [float(control_xy), float(control_xy), float(control_xy), float(control_xy), float(winch_velocity)]
        cable_length_msg.data = [float(self.tether_length), float(self.target_length), float(self.distance)]

        self.pub_pos.publish(pos_msg)
        self.pub_vel.publish(vel_msg)

        self.pub_cable_length.publish(cable_length_msg)

        self.get_logger().info(f'Objetivo: {self.target_length:.3f}, L={self.tether_length:.3f}, Error={length_error:.3f}, Winch velocity: {winch_velocity:.3f}')

def main(args=None):
    rclpy.init(args=args)
    ugv_controller = UGVController()
    rclpy.spin(ugv_controller)
    ugv_controller.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()