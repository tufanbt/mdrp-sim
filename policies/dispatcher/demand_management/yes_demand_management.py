from settings import settings
from policies.dispatcher.demand_management.dispatcher_demand_management_policy import \
    DispatcherDemandManagementPolicy
from objects.location import Location
from haversine import haversine

class YesDemandManagementPolicy(DispatcherDemandManagementPolicy):
    """Class containing the policy for the dispatcher to execute the demand management, default"""

    def execute(self, pick_up_at: Location, drop_off_at: Location, current_radius: float) -> bool:
        """Execution of the Yes Demand Management Policy"""
        radius = haversine(pick_up_at.coordinates, drop_off_at.coordinates)
        if radius > current_radius:
            return False
        else:
            return True