from asyncore import dispatcher
import logging
from dataclasses import dataclass, field
from datetime import time
from os import system
from typing import List, Dict, Any, Optional
from random import random

import pandas as pd
from simpy import Environment, Process
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from actors.courier import Courier, COURIER_ACCEPTANCE_POLICIES_MAP, COURIER_MOVEMENT_EVALUATION_POLICIES_MAP, \
    COURIER_MOVEMENT_POLICIES_MAP
from actors.dispatcher import Dispatcher, DISPATCHER_CANCELLATION_POLICIES_MAP, DISPATCHER_BUFFERING_POLICIES_MAP, \
    DISPATCHER_MATCHING_POLICIES_MAP, DISPATCHER_PREPOSITIONING_POLICIES_MAP, \
    DISPATCHER_PREPOSITIONING_EVALUATION_POLICIES_MAP, DISPATCHER_DEMAND_MANAGEMENT_POLICIES_MAP
from actors.user import User, USER_CANCELLATION_POLICIES_MAP
from ddbb.config import get_db_url
from ddbb.queries.couriers_instance_data_query import couriers_query
from ddbb.queries.orders_instance_data_query import orders_query
from objects.location import Location
from objects.vehicle import Vehicle
from settings import settings
from utils.datetime_utils import sec_to_time, time_to_query_format, time_add
from utils.logging_utils import world_log


@dataclass
class World:
    """A class to handle the simulated world"""

    env: Environment
    instance: int
    connection: Optional[Engine] = None
    couriers: List[Courier] = field(default_factory=lambda: list())
    dispatcher: Optional[Dispatcher] = None
    users: List[User] = field(default_factory=lambda: list())
    state: Optional[Process] = None

    def __post_init__(self):
        """
        The world is instantiated along with the single dispatcher and the DDBB connection.
        The World begins simulating immediately after it is created.
        """

        logging.info(f'Instance {self.instance} | Simulation started at sim time = {sec_to_time(self.env.now)}.')

        self.connection = create_engine(get_db_url(), pool_size=20, max_overflow=0, pool_pre_ping=True)
        self.dispatcher = Dispatcher(
            env=self.env,
            cancellation_policy=DISPATCHER_CANCELLATION_POLICIES_MAP[settings.DISPATCHER_CANCELLATION_POLICY],
            buffering_policy=DISPATCHER_BUFFERING_POLICIES_MAP[settings.DISPATCHER_BUFFERING_POLICY],
            matching_policy=DISPATCHER_MATCHING_POLICIES_MAP[settings.DISPATCHER_MATCHING_POLICY],
            prepositioning_policy=DISPATCHER_PREPOSITIONING_POLICIES_MAP[settings.DISPATCHER_PREPOSITIONING_POLICY],
            prepositioning_evaluation_policy=DISPATCHER_PREPOSITIONING_EVALUATION_POLICIES_MAP[settings.DISPATCHER_PREPOSITIONING_EVALUATION_POLICY],
            demand_management_policy = DISPATCHER_DEMAND_MANAGEMENT_POLICIES_MAP[settings.DISPATCHER_DEMAND_MANAGEMENT_POLICY],
            density_threshold = settings.DENSITY_THRESHOLD,
            limit_radius = settings.LIMIT_RADIUS,
            substitution_prob = settings.SUBSTITUTION_PROB
        )
        self.process = self.env.process(self._simulate())

    def _simulate(self):
        """
        State that simulates the ongoing World of the simulated environment.
        Each second the World checks the DDBB to see which couriers log on and which users place orders.
        A general log shows the ongoing simulation progress
        """

        while True:
            orders_info = self._new_orders_info(current_time=sec_to_time(self.env.now))
            if orders_info is not None:
                self._new_users_procedure(orders_info)

            couriers_info = self._new_couriers_info(current_time=sec_to_time(self.env.now))
            if couriers_info is not None:
                self._new_couriers_procedure(couriers_info)

            logging.info(
                f'Instance {self.instance} | sim time = {sec_to_time(self.env.now)} '
                f'{world_log(self.dispatcher)}'
            )

            yield self.env.timeout(delay=1)

    def _new_orders_info(self, current_time: time) -> Optional[List[Dict[str, Any]]]:
        """Method that returns the list of new users that log on at a given time"""

        if settings.CREATE_USERS_FROM <= current_time <= settings.CREATE_USERS_UNTIL:
            query = orders_query.format(
                placement_time=time_to_query_format(current_time),
                instance_id=self.instance
            )
            orders_df = pd.read_sql(sql=query, con=self.connection)
        else:
            orders_df = pd.DataFrame()

        return orders_df.to_dict('records') if not orders_df.empty else None

    def _new_couriers_info(self, current_time: time) -> Optional[List[Dict[str, Any]]]:
        """Method that returns the list of new couriers that log on at a given time"""

        if settings.CREATE_COURIERS_FROM <= current_time <= settings.CREATE_COURIERS_UNTIL:
            query = couriers_query.format(
                on_time=time_to_query_format(current_time),
                instance_id=self.instance
            )
            couriers_df = pd.read_sql(sql=query, con=self.connection)
        else:
            couriers_df = pd.DataFrame()

        return couriers_df.to_dict('records') if not couriers_df.empty else None

    def _new_users_procedure(self, orders_info: List[Dict[str, Any]]):
        """Method to establish how a new user is created in the World"""

        for order_info in orders_info:
            user = User(
                env=self.env,
                dispatcher=self.dispatcher,
                cancellation_policy=USER_CANCELLATION_POLICIES_MAP[settings.USER_CANCELLATION_POLICY],
                user_id=order_info['order_id']
            )
            
            if self.dispatcher.evaluate_demand_management(Location(lat=order_info['pick_up_lat'], lng=order_info['pick_up_lng']), Location(lat=order_info['drop_off_lat'], lng=order_info['drop_off_lng'])):
                user.submit_order_event(
                    order_id=order_info['order_id'],
                    pick_up_at=Location(lat=order_info['pick_up_lat'], lng=order_info['pick_up_lng']),
                    drop_off_at=Location(lat=order_info['drop_off_lat'], lng=order_info['drop_off_lng']),
                    placement_time=order_info['placement_time'],
                    expected_drop_off_time=order_info['expected_drop_off_time'],
                    preparation_time=order_info['preparation_time'],
                    ready_time=order_info['ready_time']
                )
            else:  
                if random() < self.dispatcher.substitution_prob:
                    user.submit_order_event(
                    order_id=order_info['order_id'],
                    pick_up_at=Location(lat=order_info['pick_up_lat2'], lng=order_info['pick_up_lng2']),
                    drop_off_at=Location(lat=order_info['drop_off_lat'], lng=order_info['drop_off_lng']),
                    placement_time=order_info['placement_time'],
                    expected_drop_off_time=order_info['expected_drop_off_time'],
                    preparation_time=order_info['preparation_time'],
                    ready_time=order_info['ready_time']
                )
                else:
                    user.save_lost_order(
                    order_id=order_info['order_id'],
                    pick_up_at=Location(lat=order_info['pick_up_lat'], lng=order_info['pick_up_lng']),
                    drop_off_at=Location(lat=order_info['drop_off_lat'], lng=order_info['drop_off_lng']),
                    placement_time=order_info['placement_time'],
                    expected_drop_off_time=order_info['expected_drop_off_time'],
                    preparation_time=order_info['preparation_time'],
                    ready_time=order_info['ready_time']
                )
            self.users.append(user)

    def _new_couriers_procedure(self, couriers_info: List[Dict[str, Any]]):
        """Method to establish how a new courier is created in the World"""

        for courier_info in couriers_info:
            courier = Courier(
                env=self.env,
                dispatcher=self.dispatcher,
                acceptance_policy=COURIER_ACCEPTANCE_POLICIES_MAP[settings.COURIER_ACCEPTANCE_POLICY],
                movement_evaluation_policy=COURIER_MOVEMENT_EVALUATION_POLICIES_MAP[
                    settings.COURIER_MOVEMENT_EVALUATION_POLICY
                ],
                movement_policy=COURIER_MOVEMENT_POLICIES_MAP[settings.COURIER_MOVEMENT_POLICY],
                courier_id=courier_info['courier_id'],
                vehicle=Vehicle.from_label(label=courier_info['vehicle']),
                location=Location(lat=courier_info['on_lat'], lng=courier_info['on_lng']),
                on_time=courier_info['on_time'],
                off_time=courier_info['off_time']
            )
            self.couriers.append(courier)

    def post_process(self):
        """Post process what happened in the World before calculating metrics for the Courier and the Order"""

        logging.info(f'Instance {self.instance} | Simulation finished at sim time = {sec_to_time(self.env.now)}.')

        for courier_id, courier in self.dispatcher.idle_couriers.copy().items():
            courier.off_time = sec_to_time(self.env.now)
            courier.log_off_event()

        warm_up_time_start = time_add(settings.SIMULATE_FROM, settings.WARM_UP_TIME)

        for order_id, order in self.dispatcher.canceled_orders.copy().items():
            if order.cancellation_time < warm_up_time_start:
                del self.dispatcher.canceled_orders[order_id]

        for order_id, order in self.dispatcher.fulfilled_orders.copy().items():
            if order.drop_off_time < warm_up_time_start:
                del self.dispatcher.fulfilled_orders[order_id]

        logging.info(f'Instance {self.instance} | Post processed the simulation.')
        system(
            f'say The simulation process for instance {self.instance}, '
            f'matching policy {settings.DISPATCHER_MATCHING_POLICY} has finished.'
        )
