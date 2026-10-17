# Included at the end of VCMI's top-level CMakeLists.txt by hook.cmake, so
# relative paths here would resolve against the VCMI source tree.
add_executable(vcmi_save_reader "${VCMI_SAVE_READER_DIR}/save_reader.cpp")
target_link_libraries(vcmi_save_reader PRIVATE vcmi)
target_include_directories(vcmi_save_reader PRIVATE "${CMAKE_SOURCE_DIR}" "${CMAKE_SOURCE_DIR}/lib")
if(MSVC)
	target_compile_options(vcmi_save_reader PRIVATE /utf-8 /bigobj)
endif()
vcmi_set_output_dir(vcmi_save_reader "")
install(TARGETS vcmi_save_reader DESTINATION ${BIN_DIR})

# Save editor: also links the server code, since it loads and saves the server state too.
add_executable(vcmi_save_edit "${VCMI_SAVE_READER_DIR}/save_edit.cpp")
target_link_libraries(vcmi_save_edit PRIVATE vcmi vcmiservercommon)
target_include_directories(vcmi_save_edit PRIVATE "${CMAKE_SOURCE_DIR}" "${CMAKE_SOURCE_DIR}/lib")
if(MSVC)
	target_compile_options(vcmi_save_edit PRIVATE /utf-8 /bigobj)
endif()
vcmi_set_output_dir(vcmi_save_edit "")
install(TARGETS vcmi_save_edit DESTINATION ${BIN_DIR})
