from __future__ import annotations

import unittest


class CommunicationGroupModuleBoundaryTests(unittest.TestCase):
    def test_public_facade_reexports_group_contracts(self) -> None:
        from app.modules.identities import public
        from app.modules.identities.communication_groups import schemas, scope, service

        self.assertIs(
            public.IdentityCommunicationGroupWrite,
            schemas.IdentityCommunicationGroupWrite,
        )
        self.assertIs(
            public.CommunicationGroupMutationError,
            service.CommunicationGroupMutationError,
        )
        self.assertIs(
            public.resolve_identity_communication_scope,
            scope.resolve_identity_communication_scope,
        )

    def test_schema_aggregate_references_group_owner(self) -> None:
        from app import schemas as aggregate
        from app.modules.identities.communication_groups.schemas import (
            IdentityCommunicationGroupMemberRead,
            IdentityCommunicationGroupRead,
            IdentityCommunicationGroupWrite,
        )

        self.assertIs(
            aggregate.IdentityCommunicationGroupMemberRead,
            IdentityCommunicationGroupMemberRead,
        )
        self.assertIs(aggregate.IdentityCommunicationGroupRead, IdentityCommunicationGroupRead)
        self.assertIs(
            aggregate.IdentityCommunicationGroupWrite,
            IdentityCommunicationGroupWrite,
        )


if __name__ == "__main__":
    unittest.main()
