"""Synthetic equipment-transition controls; these are not retail observations."""
import ctypes as C
import unittest

from resource_harness import Driver, ResourceEnv, ROOT, library


FUNCTIONS = {
    0x158e20: ('hg_player_equipment_transition', 1),
    0x1594f0: ('hg_player_condition_timer', 0),
    0x15b260: ('hg_player_flag_status', 1),
    0x15c160: ('hg_player_spawn_equipment', 1),
}
ARITIES = {
    0x159140: 1, 0x15b260: 1, 0x1676f0: 1, 0x15b6b0: 2,
    0x156ad0: 4, 0x158d90: 1, 0x158d30: 1, 0x130150: 2,
    0x1cc5b0: 3, 0x1429b0: 3, 0x120490: 2,
}


def configure(lib):
    for pc, (name, arity) in FUNCTIONS.items():
        f = getattr(lib, name)
        f.argtypes = [C.POINTER(ResourceEnv)] + [C.c_uint32] * arity
        f.restype = C.c_uint32 if pc in (0x15b260, 0x15c160) else None
    return lib


class TransitionDriver(Driver):
    """Deliberately synthetic callees, with observable reset side effects."""
    def external(self, pc, a=0, b=0, c=0, d=0):
        args = (a, b, c, d)[:ARITIES[pc]]
        # This selected caller always supplies the fifth sound argument as 1.
        self.calls.append(('call', pc, *args, *((1,) if pc == 0x156ad0 else ())))
        if pc == 0x15b260:
            return self.mem.load(0x600100)
        if pc == 0x1429b0:
            return self.mem.load(0x600104)
        if pc == 0x158d30:
            self.mem.store(a + 0x449, 255, 1)
            self.mem.store(a + 0x19, 0, 1)
            self.mem.store(0x3fa200, self.mem.load(0x600108), 1)
            self.mem.store(0x3fa201, self.mem.load(0x60010c), 1)
        return 0


class PlayerTransitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib = configure(library(output=ROOT / 'local/build/player_transition.so'))

    def test_transition_reloads_selection_after_reset(self):
        d = TransitionDriver()
        actor = 0x500000
        d.mem.store(actor + 0x449, 1, 1)
        d.mem.store(0x3fa202, 255, 1)
        d.mem.store(0x600108, 3)
        d.mem.store(0x60010c, 2)
        self.lib.hg_player_equipment_transition(C.byref(d.env), actor)
        self.assertFalse(d.errors, d.errors)
        self.assertEqual(d.mem.load(actor + 0x448, 1), 1)
        self.assertEqual(d.mem.load(actor + 0x44a, 1), 1)
        self.assertEqual(d.mem.load(actor + 0x449, 1), 255)
        self.assertEqual(d.mem.load(actor + 0x19, 1), 0)
        self.assertEqual(d.calls, [('call', 0x158d30, actor), ('call', 0x130150, 4, 0)])

    def test_changed_mode_two_selection_uses_inventory_presence(self):
        for present, expected_mode in [(0, 1), (1, 2)]:
            with self.subTest(present=present):
                d = TransitionDriver()
                actor = 0x500000
                for offset, value in [(0x448, 2), (0x449, 2), (0x50c, 1), (0x50b, 7)]:
                    d.mem.store(actor + offset, value, 1)
                d.mem.store(0x46da32 + 14, present, 2)
                d.mem.store(0x3fa202, 255, 1)
                self.lib.hg_player_equipment_transition(C.byref(d.env), actor)
                self.assertFalse(d.errors, d.errors)
                self.assertEqual(d.mem.load(actor + 0x50c, 1), 0)
                self.assertEqual(d.mem.load(actor + 0x448, 1), expected_mode)
                self.assertEqual(d.calls[0][1], 0x159140 if present else 0x158d30)
                self.assertEqual(d.mem.load(0x3fa202, 1), 7 if present else 255)

    def test_denied_transition_keeps_mode_and_orders_side_effects(self):
        for kind, sub, mask, message in [(3, 0, 0x80, 0x26070026),
                                         (0, 1, 0x400, 0x260a0026),
                                         (0, 2, 0x40, 0x26060026)]:
            with self.subTest(kind=kind, sub=sub):
                d = TransitionDriver()
                actor = 0x500000
                d.mem.store(actor + 0x449, 1, 1)
                d.mem.store(0x3fa202, 255, 1)
                d.mem.store(0x3fa200, kind, 1)
                d.mem.store(0x3fa201, sub, 1)
                d.mem.store(0x600100, 2)
                d.mem.store(0x3eaa78, 0x3f000000)  # .5 -> floor(63.5)
                self.lib.hg_player_equipment_transition(C.byref(d.env), actor)
                self.assertFalse(d.errors, d.errors)
                self.assertEqual(d.mem.load(actor + 0x448, 1), 0)
                self.assertEqual(d.calls, [('call', 0x15b260, mask),
                                          ('call', 0x1676f0, message),
                                          ('call', 0x15b6b0, mask, 2),
                                          ('call', 0x156ad0, 0x36, 0x1000, 63, 0x40, 1),
                                          ('call', 0x158d90, actor)])

    def test_countdown_requires_all_gates_and_calls_once(self):
        d = TransitionDriver()
        d.mem.store(0x46d90c, 2)
        d.mem.store(0x3f9f3c, 1, 1)
        self.lib.hg_player_condition_timer(C.byref(d.env))
        self.assertEqual(d.mem.load(0x46d90c), 2)
        d.mem.store(0x3f9f3c, 0, 1)
        for _ in range(3):
            self.lib.hg_player_condition_timer(C.byref(d.env))
        self.assertFalse(d.errors, d.errors)
        self.assertEqual(d.mem.load(0x46d90c), 0)
        self.assertEqual(d.calls, [('call', 0x1cc5b0, 0, 0x70000b80, 0x70000b80)])

    def test_flag_status_distinguishes_absent_available_and_active(self):
        d = TransitionDriver()
        d.mem.store(0x46d894, 0x80000080)
        d.mem.store(0x3f9f08, 0x80000000)
        for mask, expected in [(0, 0), (0x40, 0), (0x80, 1), (0x80000000, 2)]:
            self.assertEqual(self.lib.hg_player_flag_status(C.byref(d.env), mask), expected)
        self.assertFalse(d.errors, d.errors)
        self.assertFalse(d.calls)

    def test_equipment_allocation_failure_preserves_parent(self):
        d = TransitionDriver()
        actor = 0x500000
        d.mem.store(actor + 0x450, 0xabcdef00)
        self.assertEqual(self.lib.hg_player_spawn_equipment(C.byref(d.env), actor), 0)
        self.assertFalse(d.errors, d.errors)
        self.assertEqual(d.mem.load(actor + 0x450), 0xabcdef00)
        self.assertEqual(d.calls, [('call', 0x1429b0, 0x80, 1, 0x16b0f0)])

    def test_equipment_actor_callback_and_owner(self):
        for kind, category, callback in [(0, 0, 0x16b0f0), (3, 1, 0x173900), (6, 5, 0x173f50)]:
            with self.subTest(kind=kind):
                d = TransitionDriver()
                actor, child = 0x500000, 0x700000
                d.mem.store(0x600104, child)
                d.mem.store(actor + 0x462, kind, 1)
                d.mem.store(actor + 0x4a3, 2, 1)
                self.assertEqual(self.lib.hg_player_spawn_equipment(C.byref(d.env), actor), 1)
                self.assertFalse(d.errors, d.errors)
                self.assertEqual(d.mem.load(child + 4), category)
                self.assertEqual(d.mem.load(child + 0x1c), callback)
                self.assertEqual(d.mem.load(child + 8), kind)
                self.assertEqual(d.mem.load(child + 0x10), 2)
                self.assertEqual(d.mem.load(actor + 0x450), child)
                self.assertEqual(d.calls[-1], ('call', 0x120490, child, 0x3f92d0))


if __name__ == '__main__':
    unittest.main()
