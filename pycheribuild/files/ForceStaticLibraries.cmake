# This file should be included after project()
# See https://gitlab.kitware.com/cmake/cmake/-/issues/21942#note_921012

# Override the library suffix to only find static libraries.
set(CMAKE_SHARED_LIBRARY_SUFFIX ".a")
set(CMAKE_FIND_LIBRARY_SUFFIXES ".a")
set(CMAKE_EXTRA_SHARED_LIBRARY_SUFFIXES ".a")
