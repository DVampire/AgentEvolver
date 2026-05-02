def fibonacci_generator():
    a, b = 0, 1
    while True:
        yield a
        a, b = b, a + b

def get_nth_fibonacci(n):
    gen = fibonacci_generator()
    result = None
    for _ in range(n):
        result = next(gen)
    return result

if __name__ == '__main__':
    n = 15
    term = get_nth_fibonacci(n)
    print(f'The {n}th term of the Fibonacci sequence is: {term}')