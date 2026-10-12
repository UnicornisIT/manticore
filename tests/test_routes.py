import unittest

from tests.app_fixture import manticore
from routes import ROUTE_MODULES


class BlueprintRouteTests(unittest.TestCase):
    def test_route_groups_are_registered_as_blueprints_with_legacy_build_aliases(self):
        expected_blueprints = {module.__name__.rsplit(".", 1)[-1] for module in ROUTE_MODULES}
        self.assertTrue(expected_blueprints.issubset(manticore.app.blueprints))

        rules = list(manticore.app.url_map.iter_rules())
        for module in ROUTE_MODULES:
            blueprint_name = module.__name__.rsplit(".", 1)[-1]
            for spec in module.ROUTES:
                with self.subTest(blueprint=blueprint_name, endpoint=spec.handler, rule=spec.rule):
                    matching = [rule for rule in rules if rule.rule == spec.rule]
                    self.assertTrue(
                        any(
                            rule.endpoint == f"{blueprint_name}.{spec.handler}"
                            and not rule.build_only
                            for rule in matching
                        )
                    )
                    self.assertTrue(
                        any(rule.endpoint == spec.handler and rule.build_only for rule in matching)
                    )

    def test_public_routes_dispatch_through_blueprints_without_changing_pages(self):
        client = manticore.app.test_client()

        login = client.get("/login")
        documentation = client.get("/documentation")

        self.assertEqual(login.status_code, 200)
        self.assertIn("login-page-body", login.get_data(as_text=True))
        self.assertEqual(documentation.status_code, 200)


if __name__ == "__main__":
    unittest.main()
