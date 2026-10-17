# Pass to VCMI's configure step as
#   -DCMAKE_PROJECT_VCMI_INCLUDE=<this file>
# CMake includes it right after VCMI's project() call. Defining the save reader
# there would be too early (VCMI's flags and targets don't exist yet), so
# targets.cmake is included at the end of VCMI's top-level CMakeLists.txt
# instead. This keeps the VCMI checkout itself unmodified.
set(VCMI_SAVE_READER_DIR "${CMAKE_CURRENT_LIST_DIR}")
cmake_language(DEFER DIRECTORY "${CMAKE_SOURCE_DIR}" CALL include "${VCMI_SAVE_READER_DIR}/targets.cmake")
