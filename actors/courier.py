import random
import time
from dataclasses import dataclass, field
from typing import List, Optional, Any, Dict

from simpy import Interrupt, Event
from simpy.events import NORMAL

from actors.actor import Actor
from objects.location import Location
from objects.notification import Notification, NotificationType
from objects.order import Order
from objects.route import Route
from objects.stop import Stop, StopType
from objects.vehicle import Vehicle
from policies.courier.acceptance.absolute import AbsoluteAcceptancePolicy
from policies.courier.acceptance.courier_acceptance_policy import CourierAcceptancePolicy
from policies.courier.acceptance.random_uniform import UniformAcceptancePolicy
from policies.courier.movement.courier_movement_policy import CourierMovementPolicy
from policies.courier.movement.osrm import OSRMMovementPolicy
from policies.courier.movement.osrm_dynamic import OSRMDynamicMovementPolicy
from policies.courier.movement_evaluation.courier_movement_evaluation_policy import CourierMovementEvaluationPolicy
from policies.courier.movement_evaluation.geohash_neighbors import NeighborsMoveEvalPolicy
from policies.courier.movement_evaluation.still import StillMoveEvalPolicy
from settings import settings
from utils.datetime_utils import sec_to_time, time_diff, sec_to_hour

COURIER_ACCEPTANCE_POLICIES_MAP = {
    'uniform': UniformAcceptancePolicy(),
    'absolute': AbsoluteAcceptancePolicy()
}
COURIER_MOVEMENT_EVALUATION_POLICIES_MAP = {
    'neighbors': NeighborsMoveEvalPolicy(),
    'still': StillMoveEvalPolicy()
}
COURIER_MOVEMENT_POLICIES_MAP = {
    'osrm': OSRMMovementPolicy(),
    'osrm_dynamic': OSRMDynamicMovementPolicy()
}


@dataclass
class Courier(Actor):
    """A class used to handle a courier's state and events"""

    dispatcher: Optional[Any] = None
    acceptance_policy: Optional[CourierAcceptancePolicy] = UniformAcceptancePolicy()
    movement_evaluation_policy: Optional[CourierMovementEvaluationPolicy] = NeighborsMoveEvalPolicy()
    movement_policy: Optional[CourierMovementPolicy] = OSRMMovementPolicy()

    acceptance_rate: float = random.uniform(settings.COURIER_MIN_ACCEPTANCE_RATE, 1)
    accepted_notifications: List[Notification] = field(default_factory=lambda: list())
    active_route: Optional[Route] = None
    active_stop: Optional[Stop] = None
    courier_id: Optional[int] = None
    earnings: Optional[float] = None
    fulfilled_orders: List[int] = field(default_factory=lambda: list())
    guaranteed_compensation: Optional[bool] = None
    location: Optional[Location] = None
    log_off_scheduled: Optional[bool] = False
    on_time: time = None
    off_time: time = None
    rejected_orders: List[int] = field(default_factory=lambda: list())
    utilization_time: float = 0
    vehicle: Optional[Vehicle] = Vehicle.MOTORCYCLE

    def __post_init__(self):
        """Immediately after the courier logs on, the log off is scheduled and it starts idling"""

        self._log(f'Actor {self.courier_id} logged on')

        self._schedule_log_off_event()
        self.state = self.env.process(self._idle_state())

    def _idle_state(self):
        """State that simulates a courier being idle / waiting"""

        self.condition = 'idle'

        self._log(f'Courier {self.courier_id} begins idling')

        try:
            self.dispatcher.courier_idle_event(courier=self)

        except Interrupt:
            pass

        while True:
            try:
                yield self.env.timeout(delay=settings.COURIER_WAIT_TO_MOVE)
                yield self.env.process(self._evaluate_movement_event())

            except Interrupt:
                break

    def _moving_state(self, destination: Location):
        """State detailing how a courier moves to a destination"""

        self.condition = 'moving'
        state_start = sec_to_time(self.env.now)
        self.dispatcher.courier_moving_event(courier=self)
        yield self.env.process(
            self.movement_policy.execute(
                origin=self.location,
                destination=destination,
                env=self.env,
                courier=self
            )
        )
        self.utilization_time += time_diff(sec_to_time(self.env.now), state_start)

    def _picking_up_state(self, orders: Dict[int, Order]):
        """State that simulates a courier picking up stuff at the pick up location"""

        self.condition = 'picking_up'

        self._log(f'Courier {self.courier_id} begins pick up state')

        state_start = sec_to_time(self.env.now)

        try:
            self.dispatcher.courier_picking_up_event(courier=self)
            self.dispatcher.orders_in_store_event(orders)

        except Interrupt:
            pass

        try:
            service_time = max(order.pick_up_service_time for order in orders.values())
            latest_ready_time = max(order.ready_time for order in orders.values())
            waiting_time = time_diff(latest_ready_time, sec_to_time(self.env.now))
            yield self.env.timeout(delay=service_time + max(0, waiting_time))

        except Interrupt:
            pass

        self.utilization_time += time_diff(sec_to_time(self.env.now), state_start)

        self._log(f'Courier {self.courier_id} finishes pick up state')

        self.dispatcher.orders_picked_up_event(orders)

    def _dropping_off_state(self, orders: Dict[int, Order]):
        """State that simulates a courier dropping off stuff at the drop off location"""

        self.condition = 'dropping_off'

        self._log(f'Courier {self.courier_id} begins drop off state of orders {list(orders.keys())}')

        state_start = sec_to_time(self.env.now)
        self.dispatcher.courier_dropping_off_event(courier=self)
        service_time = max(order.drop_off_service_time for order in orders.values())
        yield self.env.timeout(delay=service_time)
        self.utilization_time += time_diff(sec_to_time(self.env.now), state_start)

        self._log(f'Courier {self.courier_id} finishes drop off state of orders {list(orders.keys())}')

        self.dispatcher.orders_dropped_off_event(orders=orders, courier=self)

    def _evaluate_movement_event(self):
        """Event detailing how a courier evaluates to move about the city"""

        destination = self.movement_evaluation_policy.execute(current_location=self.location)

        if destination is not None:

            self._log(f'Courier {self.courier_id} decided to move from {self.location} to {destination}')

            yield self.env.process(self._moving_state(destination))

            self._log(f'Courier {self.courier_id} finished relocating and is now at {self.location}')

            self.condition = 'idle'
            self.dispatcher.courier_idle_event(courier=self)

        else:
            self._log(f'Courier {self.courier_id} decided not to move')

    def notification_event(self, notification: Notification):
        """Event detailing how a courier handles a notification"""

        self._log(f'Courier {self.courier_id} received a {notification.type.label} notification')

        if self.condition in ['idle', 'picking_up']:
            try:
                self.state.interrupt()

            except:
                pass

        accepts_notification = yield self.env.process(
            self.acceptance_policy.execute(self.acceptance_rate, self.env)
        )

        if accepts_notification:
            self._log(f'Courier {self.courier_id} accepted a {notification.type.label} notification.')

            self.dispatcher.notification_accepted_event(notification=notification, courier=self)

            if (
                    (isinstance(notification.instruction, list) or bool(notification.instruction.orders)) and
                    self.active_route is not None
            ) or notification.type == NotificationType.PREPOSITIONING:
                self.env.process(self._execute_active_route())

            else:
                self.state = self.env.process(self._idle_state())

        else:
            self._log(f'Courier {self.courier_id} rejected a {notification.type.label} notification.')

            self.dispatcher.notification_rejected_event(notification=notification, courier=self)

            if self.condition == 'idle':
                self.state = self.env.process(self._idle_state())

            elif self.condition == 'picking_up':
                self.env.process(self._execute_active_route())

    def log_off_event(self):
        """Event detailing how a courier logs off of the system, ensuring earnings are calculated"""

        self._log(f'Courier {self.courier_id} is going to log off')

        if self.active_route is None and self.active_stop is None:
            self.earnings = self._calculate_earnings()

            try:
                self.state.interrupt()

            except:
                pass

            self.condition = 'logged_off'
            self.dispatcher.courier_log_off_event(courier=self)

        else:
            self.log_off_scheduled = True

            self._log(f'Courier {self.courier_id} is scheduled to log off after completing current instructions')

    def _execute_stop(self, stop: Stop):
        """State to execute a stop"""

        self.active_stop = stop

        self._log(
            f'Courier {self.courier_id} is at stop of type {self.active_stop.type.label} '
            f'with orders {list(stop.orders.keys())}, on location {stop.location}'
        )

        service_state = self._picking_up_state if stop.type == StopType.PICK_UP else self._dropping_off_state
        yield self.env.process(service_state(orders=stop.orders))

        stop.visited = True

    def _execute_active_route(self):
        """State to execute the remainder of a route"""

        stops_remaining = [stop for stop in self.active_route.stops if not stop.visited]

        self._log(f'Courier {self.courier_id} has {len(stops_remaining)} stops remaining')

        for stop in stops_remaining:
            if self.active_stop != stop:
                self._log(f'Courier {self.courier_id} will move to next stop')

                yield self.env.process(self._moving_state(destination=stop.location))

            if stop.type != StopType.PREPOSITION:
                yield self.env.process(self._execute_stop(stop))

        self.active_route = None
        self.active_stop = None

        self._log(f'Courier {self.courier_id} finishes route execution')

        if self.log_off_scheduled:
            self.log_off_event()

        else:
            self.state = self.env.process(self._idle_state())

    def _log_off_callback(self, event: Event):
        """Callback to activate the log off event"""

        self.log_off_event()
        event.succeed()
        event.callbacks = []

    def _schedule_log_off_event(self):
        """Method that allows the courier to schedule the log off time"""

        log_off_event = Event(env=self.env)
        log_off_event.callbacks.append(self._log_off_callback)
        log_off_delay = time_diff(self.off_time, self.on_time)
        self.env.schedule(event=log_off_event, priority=NORMAL, delay=log_off_delay)

    def _calculate_earnings(self) -> float:
        """Method to calculate earnings after the shift ends"""

        delivery_earnings = len(self.fulfilled_orders) * settings.COURIER_EARNINGS_PER_ORDER
        guaranteed_earnings = sec_to_hour(time_diff(self.off_time, self.on_time)) * settings.COURIER_EARNINGS_PER_HOUR

        if guaranteed_earnings > delivery_earnings > 0:
            self.guaranteed_compensation = True
            earnings = guaranteed_earnings

        else:
            self.guaranteed_compensation = False
            earnings = delivery_earnings

        self._log(
            f'Courier {self.courier_id} received earnings of ${round(earnings, 2)} '
            f'for {len(self.fulfilled_orders)} orders during the complete shift'
        )

        return earnings

    def calculate_metrics(self) -> Dict[str, Any]:
        """Method to calculate the metrics of a courier"""

        courier_delivery_earnings = len(self.fulfilled_orders) * settings.COURIER_EARNINGS_PER_ORDER
        shift_duration = time_diff(self.off_time, self.on_time)

        if shift_duration > 0:
            courier_utilization = self.utilization_time / shift_duration
            courier_orders_delivered_per_hour = len(self.fulfilled_orders) / sec_to_hour(shift_duration)
            courier_bundles_picked_per_hour = len(self.accepted_notifications) / sec_to_hour(shift_duration)

        else:
            courier_utilization, courier_orders_delivered_per_hour, courier_bundles_picked_per_hour = 0, 0, 0

        return {
            'courier_id': self.courier_id,
            'on_time': self.on_time,
            'off_time': self.off_time,
            'fulfilled_orders': len(self.fulfilled_orders),
            'earnings': self.earnings,
            'utilization_time': self.utilization_time,
            'accepted_notifications': len(self.accepted_notifications),
            'guaranteed_compensation': self.guaranteed_compensation,
            'courier_utilization': courier_utilization,
            'courier_delivery_earnings': courier_delivery_earnings,
            'courier_compensation': self.earnings,
            'courier_orders_delivered_per_hour': courier_orders_delivered_per_hour,
            'courier_bundles_picked_per_hour': courier_bundles_picked_per_hour
        }
