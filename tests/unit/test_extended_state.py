"""Unit tests for BagItem, PCPokemon, and ExtendedState models."""


from src.agent.models import BagItem, ExtendedState, PCPokemon


class TestBagItem:
    def test_from_dict_full(self):
        item = BagItem.from_dict({"item_id": 4, "name": "Poke Ball", "quantity": 5})
        assert item.item_id == 4
        assert item.name == "Poke Ball"
        assert item.quantity == 5

    def test_from_dict_fallback_name(self):
        item = BagItem.from_dict({"item_id": 9999, "quantity": 1})
        assert item.name == "Item#9999"

    def test_from_dict_default_quantity(self):
        item = BagItem.from_dict({"item_id": 13, "name": "Potion"})
        assert item.quantity == 1


class TestPCPokemon:
    def test_from_dict_full(self):
        mon = PCPokemon.from_dict({"box": 1, "slot": 3, "nickname": "TORCHIC"})
        assert mon.box == 1
        assert mon.slot == 3
        assert mon.nickname == "TORCHIC"

    def test_from_dict_default_nickname(self):
        mon = PCPokemon.from_dict({"box": 2, "slot": 1})
        assert mon.nickname == "???"


class TestExtendedState:
    def _make_payload(self):
        return {
            "bag": {
                "items": [
                    {"item_id": 13, "name": "Potion", "quantity": 3},
                    {"item_id": 22, "name": "Super Potion", "quantity": 1},
                ],
                "pokeballs": [
                    {"item_id": 4, "name": "Poke Ball", "quantity": 10},
                ],
                "key_items": [],
                "tms_hms": [],
                "berries": [],
            },
            "pc_boxes": [
                {
                    "box": 1,
                    "pokemon": [
                        {"box": 1, "slot": 1, "nickname": "ZIGZAGOON"},
                        {"box": 1, "slot": 5, "nickname": "WURMPLE"},
                    ],
                },
            ],
        }

    def test_from_dict_bag_pockets(self):
        es = ExtendedState.from_dict(self._make_payload())
        assert "items" in es.bag
        assert "pokeballs" in es.bag
        assert len(es.bag["items"]) == 2
        assert len(es.bag["pokeballs"]) == 1

    def test_from_dict_bag_items_are_bagitem(self):
        es = ExtendedState.from_dict(self._make_payload())
        item = es.bag["items"][0]
        assert isinstance(item, BagItem)
        assert item.item_id == 13
        assert item.name == "Potion"
        assert item.quantity == 3

    def test_from_dict_empty_pocket(self):
        es = ExtendedState.from_dict(self._make_payload())
        assert es.bag["key_items"] == []
        assert es.bag["tms_hms"] == []

    def test_from_dict_pc_boxes(self):
        es = ExtendedState.from_dict(self._make_payload())
        assert len(es.pc_boxes) == 1
        box = es.pc_boxes[0]
        assert box["box"] == 1
        assert len(box["pokemon"]) == 2

    def test_from_dict_pc_pokemon_are_pcpokemon(self):
        es = ExtendedState.from_dict(self._make_payload())
        mon = es.pc_boxes[0]["pokemon"][0]
        assert isinstance(mon, PCPokemon)
        assert mon.box == 1
        assert mon.slot == 1
        assert mon.nickname == "ZIGZAGOON"

    def test_from_dict_empty(self):
        es = ExtendedState.from_dict({})
        assert es.bag == {}
        assert es.pc_boxes == []

    def test_from_dict_no_pc_boxes(self):
        es = ExtendedState.from_dict({"bag": {}, "pc_boxes": []})
        assert es.pc_boxes == []

    def test_from_dict_unknown_item_fallback_name(self):
        payload = {"bag": {"items": [{"item_id": 9999, "quantity": 1}]}, "pc_boxes": []}
        es = ExtendedState.from_dict(payload)
        assert es.bag["items"][0].name == "Item#9999"

    def test_from_dict_multiple_pc_boxes(self):
        payload = {
            "bag": {},
            "pc_boxes": [
                {"box": 1, "pokemon": [{"box": 1, "slot": 1, "nickname": "A"}]},
                {"box": 3, "pokemon": [{"box": 3, "slot": 2, "nickname": "B"}]},
            ],
        }
        es = ExtendedState.from_dict(payload)
        assert len(es.pc_boxes) == 2
        assert es.pc_boxes[1]["box"] == 3
