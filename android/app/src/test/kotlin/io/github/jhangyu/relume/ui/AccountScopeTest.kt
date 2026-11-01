package io.github.jhangyu.relume.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Sign-out resets every account-bound state; a field missing from a hand-kept list leaked the previous account's data. */
class AccountScopeTest {
    @Test
    fun resetRestoresEveryStateToItsInitialValue() {
        val scope = AccountScope()
        val list = scope.state(emptyList<Int>())
        val text = scope.state<String?>(null)
        list.value = listOf(1)
        text.value = "previous account"
        scope.reset()
        assertEquals(emptyList<Int>(), list.value)
        assertNull(text.value)
    }
}
