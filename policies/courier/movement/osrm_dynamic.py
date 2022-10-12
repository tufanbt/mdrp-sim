from haversine import haversine
from simpy import Environment

from objects.location import Location
from policies.courier.movement.courier_movement_policy import CourierMovementPolicy
from services.osrm_service import OSRMService

from utils.datetime_utils import sec_to_time

class OSRMDynamicMovementPolicy(CourierMovementPolicy):
    """
    Class containing the policy that implements the movement of a courier to a destination.
    It uses the Open Source Routing Machine with Open Street Maps.
    """
    speed_coeff = {0: 1,
                   1: 1,
                   2: 1,
                   3: 1,
                   4: 1,
                   5: 1,
                   6: 1,
                   7: 1,
                   8: 1,
                   9: 1.13,
                   10: 1.04,
                   11: 1.0,
                   12: 0.91,
                   13: 0.90,
                   14: 0.93,
                   15: 0.95,
                   16: 1.02,
                   17: 1.0,
                   18: 0.91,
                   19: 0.87,
                   20: 0.88,
                   21: 0.99,
                   22: 1.23,
                   23: 1.23
                   }
    
    def execute(self, origin: Location, destination: Location, env: Environment, courier):
        """Execution of the Movement Policy"""

        route = OSRMService.get_route(origin, destination)

        for ix in range(len(route.stops) - 1):
            stop = route.stops[ix]
            next_stop = route.stops[ix + 1]

            distance = haversine(stop.location.coordinates, next_stop.location.coordinates)
            velocity = courier.vehicle.average_velocity * self.speed_coeff[sec_to_time(env.now).hour]
            time = int(distance / velocity)

            yield env.timeout(delay=time)

            courier.location = next_stop.location
