add_executable(tidylib_tests test_tidy.cpp)
target_link_libraries(tidylib_tests PRIVATE tidylib)
add_test(NAME tidylib_tests COMMAND tidylib_tests)
