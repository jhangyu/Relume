package io.github.jhangyu.relume.ui

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** Past bug: only bare names ("social") matched, so the catalog's real category names all fell back to one hue. */
class CategoryHueTest {
    @Test
    fun catalogCategoryNamesGetTheirHue() {
        assertEquals(CategoryHue.Social, categoryHue("Social & Communication"))
        assertEquals(CategoryHue.Tools, categoryHue("Tools & Utilities"))
        assertEquals(CategoryHue.Video, categoryHue("Photography & Design"))
        assertEquals(CategoryHue.Music, categoryHue("Music & Audio"))
        assertEquals(CategoryHue.Productivity, categoryHue("Productivity"))
        assertEquals(CategoryHue.Maps, categoryHue("Maps & Navigation"))
        assertEquals(CategoryHue.News, categoryHue("News & Magazines"))
        assertEquals(CategoryHue.Personalization, categoryHue("Personalization"))
        assertEquals(CategoryHue.Lifestyle, categoryHue("Health & Fitness"))
        assertEquals(CategoryHue.Lifestyle, categoryHue("Shopping"))
    }

    @Test
    fun unknownOrMissingCategoryIsNeutral() {
        assertNull(categoryHue("Weather"))
        assertNull(categoryHue(null))
    }
}
