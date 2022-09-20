import platform
from socket import gethostname
from uuid import getnode

from common import OperatingSystem
from common.network.network_utils import get_network_interfaces
from monkey_island.cc.models import Machine

from . import IMachineRepository, UnknownRecordError


def initialize_machine_repository(machine_repository: IMachineRepository):
    """
    Populate an IMachineRepository with island machine data

    If the island is not already present in the IMachineRepository, add it.

    :param machine_repository: The repository to populate
    :raises StorageError: If an error occurs while attempting to store data in the repository
    """
    hardware_id = getnode()

    try:
        machine_repository.get_machine_by_hardware_id(hardware_id)
    except UnknownRecordError:
        machine = Machine(
            id=machine_repository.get_new_id(),
            hardware_id=hardware_id,
            island=True,
            network_interfaces=get_network_interfaces(),
            operating_system=OperatingSystem(platform.system().lower()),
            operating_system_version=platform.version(),
            hostname=gethostname(),
        )
        machine_repository.upsert_machine(machine)
