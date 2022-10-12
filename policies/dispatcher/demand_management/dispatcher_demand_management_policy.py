from policies.policy import Policy
from objects.location import Location

class DispatcherDemandManagementPolicy(Policy):
    """Class that establishes how the dispatcher handles demand management decisions"""

    def execute(self,  pick_up_at: Location, drop_off_at: Location, current_radius: float) -> bool:
        """Implementation of the policy"""

        pass
