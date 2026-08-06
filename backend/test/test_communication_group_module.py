from __future__ import annotations

import unittest


class CommunicationGroupModuleCompatibilityTests(unittest.TestCase):
    def test_legacy_api_path_reexports_module_adapter(self) -> None:
        from app.api.communication_groups import (
            create_communication_group as legacy_create,
            delete_communication_group as legacy_delete,
            list_communication_groups as legacy_list,
            router as legacy_router,
            update_communication_group as legacy_update,
        )
        from app.modules.identities.communication_groups.api import (
            create_communication_group,
            delete_communication_group,
            list_communication_groups,
            router,
            update_communication_group,
        )

        self.assertIs(legacy_create, create_communication_group)
        self.assertIs(legacy_delete, delete_communication_group)
        self.assertIs(legacy_list, list_communication_groups)
        self.assertIs(legacy_router, router)
        self.assertIs(legacy_update, update_communication_group)

    def test_legacy_schema_path_reexports_module_types(self) -> None:
        from app.modules.identities.public import (
            IdentityCommunicationGroupMemberRead,
            IdentityCommunicationGroupRead,
            IdentityCommunicationGroupWrite,
        )
        from app.schemas.communication_group import (
            IdentityCommunicationGroupMemberRead as LegacyMemberRead,
            IdentityCommunicationGroupRead as LegacyGroupRead,
            IdentityCommunicationGroupWrite as LegacyGroupWrite,
        )
        from app.schemas import (
            IdentityCommunicationGroupMemberRead as AggregatedMemberRead,
            IdentityCommunicationGroupRead as AggregatedGroupRead,
            IdentityCommunicationGroupWrite as AggregatedGroupWrite,
        )

        self.assertIs(LegacyMemberRead, IdentityCommunicationGroupMemberRead)
        self.assertIs(LegacyGroupRead, IdentityCommunicationGroupRead)
        self.assertIs(LegacyGroupWrite, IdentityCommunicationGroupWrite)
        self.assertIs(AggregatedMemberRead, IdentityCommunicationGroupMemberRead)
        self.assertIs(AggregatedGroupRead, IdentityCommunicationGroupRead)
        self.assertIs(AggregatedGroupWrite, IdentityCommunicationGroupWrite)

    def test_legacy_service_path_reexports_module_functions(self) -> None:
        from app.modules.identities.public import (
            CommunicationGroupMutationError,
            create_communication_group_record,
            delete_communication_group_record,
            get_communication_group_record,
            list_communication_group_records,
            serialize_communication_group,
            update_communication_group_record,
        )
        from app.modules.identities.communication_groups.service import (
            get_communication_group_or_raise,
        )
        from app.services.communication_group_mutations import (
            CommunicationGroupMutationError as LegacyMutationError,
            create_communication_group_record as legacy_create,
            delete_communication_group_record as legacy_delete,
            get_communication_group_or_raise as legacy_get_or_raise,
            get_communication_group_record as legacy_get,
            list_communication_group_records as legacy_list,
            serialize_communication_group as legacy_serialize,
            update_communication_group_record as legacy_update,
        )

        self.assertIs(LegacyMutationError, CommunicationGroupMutationError)
        self.assertIs(legacy_create, create_communication_group_record)
        self.assertIs(legacy_delete, delete_communication_group_record)
        self.assertIs(legacy_get, get_communication_group_record)
        self.assertIs(legacy_get_or_raise, get_communication_group_or_raise)
        self.assertIs(legacy_list, list_communication_group_records)
        self.assertIs(legacy_serialize, serialize_communication_group)
        self.assertIs(legacy_update, update_communication_group_record)

    def test_legacy_scope_path_reexports_module_helpers(self) -> None:
        from app.modules.identities.public import (
            CommunicationGroupCleanupResult,
            IdentityCommunicationScope,
            cleanup_communication_group_after_identity_delete,
            resolve_identity_communication_scope,
        )
        from app.services.identity_communication_groups import (
            CommunicationGroupCleanupResult as LegacyCleanupResult,
            IdentityCommunicationScope as LegacyScope,
            cleanup_communication_group_after_identity_delete as legacy_cleanup,
            resolve_identity_communication_scope as legacy_resolve,
        )

        self.assertIs(LegacyCleanupResult, CommunicationGroupCleanupResult)
        self.assertIs(LegacyScope, IdentityCommunicationScope)
        self.assertIs(legacy_cleanup, cleanup_communication_group_after_identity_delete)
        self.assertIs(legacy_resolve, resolve_identity_communication_scope)


if __name__ == "__main__":
    unittest.main()
