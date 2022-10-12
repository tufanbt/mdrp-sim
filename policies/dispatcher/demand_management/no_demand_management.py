from settings import settings
from policies.dispatcher.demand_management.dispatcher_demand_management_policy import \
    DispatcherDemandManagementPolicy
from objects.location import Location

class NoDemandManagementPolicy(DispatcherDemandManagementPolicy):
    """Class containing the policy for the dispatcher to execute no demand management, default"""

    def execute(self, pick_up_at: Location, drop_off_at: Location, current_radius: float) -> bool:
        """Execution of the No Demand Management Policy"""

        return True
