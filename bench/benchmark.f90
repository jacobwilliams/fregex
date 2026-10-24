program benchmark
!! Times fregex on the cases written by tools/benchmark.py, which also times Python's `re` on the
!! same cases and builds the comparison table.
!!
!! Usage: benchmark DIR [MIN_SECONDS REPEATS]
!!
!! DIR/cases.txt has one case per line: `name op flags pattern-file subject-file repl-file`, with
!! files relative to DIR. For each case one line is printed: `name seconds-per-op check`, where
!! the check value (match start, match count, output length, ...) is compared with Python's.
!! Like Python's `timeit`, the number of loops is chosen so that one run takes at least
!! MIN_SECONDS, and the best of REPEATS runs is reported.
use iso_fortran_env, only: int64, real64, error_unit
use fregex
implicit none

character(len=:), allocatable :: dir
character(len=256) :: case_name, case_op, patf, subf, replf, arg
integer :: unit, ios, case_flags, repeats
real(real64) :: min_seconds

min_seconds = 0.2_real64
repeats = 5
if (command_argument_count() < 1) then
    write (error_unit, '(a)') 'usage: benchmark DIR [MIN_SECONDS REPEATS]'
    error stop
end if
call get_command_argument(1, arg)
dir = trim(arg)//'/'
if (command_argument_count() >= 3) then
    call get_command_argument(2, arg)
    read (arg, *) min_seconds
    call get_command_argument(3, arg)
    read (arg, *) repeats
end if

open (newunit=unit, file=dir//'cases.txt', status='old', action='read')
do
    read (unit, *, iostat=ios) case_name, case_op, case_flags, patf, subf, replf
    if (ios /= 0) exit
    call run_case(trim(case_name), trim(case_op), case_flags, read_file(dir//trim(patf)), &
                  read_file(dir//trim(subf)), read_file(dir//trim(replf)))
end do
close (unit)

contains

subroutine run_case(name, op, flags, pattern, subject, repl)
    character(len=*), intent(in) :: name, op, pattern, subject, repl
    integer, intent(in) :: flags

    integer(int64), parameter :: steps(0:2) = [1_int64, 2_int64, 5_int64]
    type(regex_t) :: re
    integer(int64) :: n
    integer :: check, r, k
    real(real64) :: t, best

    re = regex(pattern, flags)
    n = 1
    k = 0
    do   ! 1, 2, 5, 10, 20, 50, ... loops until one run is long enough
        t = time_loops(re, op, flags, pattern, subject, repl, n, check)
        if (t >= min_seconds) exit
        k = k + 1
        n = steps(mod(k, 3))*10_int64**(k/3)
    end do
    best = t
    do r = 2, repeats
        best = min(best, time_loops(re, op, flags, pattern, subject, repl, n, check))
    end do
    write (*, '(a,1x,es16.9,1x,i0)') name, best/real(n, real64), check
end subroutine run_case

real(real64) function time_loops(re, op, flags, pattern, subject, repl, n, check) result(seconds)
    !! Runs the operation n times and returns the elapsed time. `check` summarizes the last result.
    type(regex_t), intent(inout) :: re
    character(len=*), intent(in) :: op, pattern, subject, repl
    integer, intent(in) :: flags
    integer(int64), intent(in) :: n
    integer, intent(out) :: check

    integer(int64) :: i, c0, c1, rate
    type(regex_t) :: re2
    type(match_t) :: m
    type(match_t), allocatable :: matches(:)
    type(regex_string_t), allocatable :: parts(:)
    character(len=:), allocatable :: out
    logical :: found

    call system_clock(c0, rate)
    select case (op)
    case ('compile')
        do i = 1, n
            re2 = regex(pattern, flags)
        end do
        check = re2%group_count()
    case ('search')
        do i = 1, n
            found = re%search(subject, m)
        end do
        check = merge(m%start(), 0, found)
    case ('fullmatch')
        do i = 1, n
            found = re%fullmatch(subject, m)
        end do
        check = merge(1, 0, found)
    case ('find_all')
        do i = 1, n
            matches = re%find_all(subject)
        end do
        check = size(matches)
    case ('sub')
        do i = 1, n
            out = re%sub(subject, repl)
        end do
        check = len(out)
    case ('split')
        do i = 1, n
            parts = re%split(subject)
        end do
        check = size(parts)
    case default
        write (error_unit, '(a)') 'unknown op: '//op
        error stop
    end select
    call system_clock(c1)
    seconds = real(c1 - c0, real64)/real(rate, real64)
end function time_loops

function read_file(path) result(text)
    character(len=*), intent(in) :: path
    character(len=:), allocatable :: text
    integer :: u, n

    open (newunit=u, file=path, access='stream', form='unformatted', status='old', action='read')
    inquire (unit=u, size=n)
    allocate (character(len=n) :: text)
    if (n > 0) read (u) text
    close (u)
end function read_file

end program benchmark
