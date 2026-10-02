class PlannerError(Exception):
    """Base error for the route planner. `status_code` maps to the HTTP response."""

    status_code = 400
    code = "planner_error"

    def __init__(self, message):
        super().__init__(message)
        self.message = message


class LocationNotFound(PlannerError):
    status_code = 400
    code = "location_not_found"


class RoutingUnavailable(PlannerError):
    status_code = 502
    code = "routing_unavailable"


class RouteNotFound(PlannerError):
    status_code = 422
    code = "route_not_found"


class NoFeasibleFuelPlan(PlannerError):
    status_code = 422
    code = "no_feasible_fuel_plan"
