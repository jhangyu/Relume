package io.github.jhangyu.relume.install

/** Set by `MainActivity.onStart/onStop`: a confirm prompt starts directly when visible, else via a notification (spec 9.4). */
object AppVisibility {
    @Volatile
    var visible: Boolean = false
}
