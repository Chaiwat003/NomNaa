# Function to calculate average of a list of numbers

from typing import List, Union

def calculate_average(numbers: List[Union[int, float]]) -> float:
    """
    Calculate the average (arithmetic mean) of a list of numbers.
    
    Args:
        numbers: A list of integers or floats.
    
    Returns:
        The average of all numbers in the list, or 0 if the list is empty.
    
    Example:
        >>> calculate_average([10, 20, 30, 40, 50])
        30.0
        >>> calculate_average([])
        0
    """
    if not numbers:
        return 0
    return sum(numbers) / len(numbers)

# Example usage
numbers = [10, 20, 30, 40, 50]
average = calculate_average(numbers)
print(f"The average is: {average}")